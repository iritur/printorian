"""`GET /health/printing`, over HTTP — the answer something is about to act on.

Its own file rather than an addition to `test_health_api.py`, which covers the
other three and is already two thirds of the way to the 400-line gate. The split
is by responsibility: everything here is about one endpoint whose consumer is
`deploy/reboot-guard.sh` on the farm host, and whose wrong answer is a destroyed
print rather than a wrong number on a screen.

The case that matters most is `test_a_database_that_cannot_be_read_is_not_an_idle_farm`.
Every other case here would still pass if the endpoint answered three zeros
whenever it could not read anything, and that answer is the one that authorises a
reboot at hour eleven of a twelve-hour plate.

The `client` fixture is declared here rather than imported. Support modules under
`tests/api/` hold helpers and deliberately never fixtures (`_journal_support.py`,
`_catalog_support.py`, `_fleet_metrics_support.py` each say so): importing a
fixture by name shadows the parameter of every test that takes it. A module that
needs a client declares one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.production.policies import JobStatus
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app
from tests.unit._dashboard_support import a_job, an_order_id


class _TestDatabase:
    """Stands in for `core.db.Database`, per the idiom the API tests use."""

    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, poolclass=NullPool)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)

    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    wire_app(
        app,
        settings=settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http

    await database.dispose()


async def stage(db: AsyncSession, *statuses: JobStatus) -> None:
    """One job per status named. None of them names a printer, which is deliberate:
    the endpoint's answer must not depend on a machine being attached, and the case
    below that turns on it says so where it is asserted."""
    order_id = await an_order_id(db)
    for status in statuses:
        db.add(a_job(order_id, status=status, grams=Decimal(10)))
    await db.commit()


# ------------------------------------------------------------ the affirmative


async def test_an_idle_farm_answers_that_it_is_quiet(client: AsyncClient) -> None:
    """200 is the only affirmative this endpoint gives, and it is given only for a
    reading that was taken and came back with nothing on a machine."""
    response = await client.get("/health/printing")

    assert response.status_code == 200
    assert response.json() == {
        "status": "quiet",
        "in_flight": {"assigned": 0, "dispatching": 0, "printing": 0},
    }


async def test_work_that_is_not_on_a_machine_does_not_defer_a_reboot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A full queue and a full history are both quiet.

    Nothing before a plate reaches a printer and nothing after it comes off one can
    be damaged by a power cut, so a farm with a week of backlog still patches. A
    veto that fired on the queue would sit on for as long as the queue did.
    """
    await stage(
        db_session,
        JobStatus.PENDING,
        JobStatus.READY,
        JobStatus.ON_HOLD,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    )

    response = await client.get("/health/printing")

    assert response.status_code == 200
    assert response.json()["status"] == "quiet"


async def test_a_plan_is_reported_without_vetoing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two claims in one body, and they pull in opposite directions.

    An ASSIGNED job does not stop a reboot — nothing has been sent to a machine, and
    the planner re-makes the assignment after a restart; a veto here would let one
    wedged job stop a host patching for ever. But the count is still reported, so an
    operator reading `quiet` beside a queue that is not empty can see why.
    """
    await stage(db_session, JobStatus.ASSIGNED)

    response = await client.get("/health/printing")

    assert response.status_code == 200
    assert response.json()["status"] == "quiet"
    assert response.json()["in_flight"]["assigned"] == 1


# ------------------------------------------------------------ the veto


async def test_a_running_print_defers_the_reboot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await stage(db_session, JobStatus.PRINTING)

    response = await client.get("/health/printing")

    assert response.status_code == 503
    assert response.json()["status"] == "in_flight"
    assert response.json()["in_flight"]["printing"] == 1


async def test_a_half_finished_upload_defers_the_reboot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The case issue #17's own wording — "while any job is printing" — would have
    let through. A dispatch dying half-way leaves a machine holding part of a plate
    file, which is the one genuinely irreversible moment in the sequence, and
    `policies.py` gives the state its own name for exactly that reason."""
    await stage(db_session, JobStatus.DISPATCHING)

    response = await client.get("/health/printing")

    assert response.status_code == 503
    assert response.json()["status"] == "in_flight"
    assert response.json()["in_flight"]["dispatching"] == 1


async def test_a_print_whose_printer_was_cleared_still_defers_the_reboot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The test that fails the moment somebody narrows the query with
    `printer_id IS NOT NULL`.

    `print_jobs.printer_id` is `SET NULL`, so a printer retired under a running job
    leaves a PRINTING row naming no machine. It is still a print, and dropping it
    would turn an absent value into an idle farm on the one path where that
    authorises pulling the plug.
    """
    order_id = await an_order_id(db_session)
    # Spelled out rather than left to `a_job`'s default, because the NULL is the
    # whole case: a default that changed would otherwise quietly stop testing it.
    db_session.add(a_job(order_id, status=JobStatus.PRINTING, grams=Decimal(10), printer_id=None))
    await db_session.commit()

    response = await client.get("/health/printing")

    assert response.status_code == 503
    assert response.json()["in_flight"]["printing"] == 1


# ------------------------------------------------------------ the irreversible path


class _UnreadableDatabase:
    """A database whose sessions cannot be opened at all.

    `OSError` rather than a SQLAlchemy error on purpose: the failures this endpoint
    has to survive on a farm host are a full disk, a dropped socket and a container
    that has not come back yet, and none of those arrive wearing a database library's
    exception type.
    """

    def session(self) -> AsyncIterator[AsyncSession]:
        raise OSError("the socket is gone")


def break_the_database(client: AsyncClient) -> None:
    """Put an unreadable database where the endpoint looks for one.

    Reaching through the transport for the app, the idiom `test_health_api.py`
    documents at `publish()`: the fixture yields only the client, and a second
    fixture returning the pair would touch every case in this file to serve one.
    """
    client._transport.app.state.database = _UnreadableDatabase()  # type: ignore[attr-defined]


async def test_a_database_that_cannot_be_read_is_not_an_idle_farm(
    client: AsyncClient,
) -> None:
    """**The irreversible path**, and the reason this endpoint exists in this shape.

    Every other case in this file would still pass if a failed read answered three
    zeros. That answer reads as "the farm is doing nothing", and the thing reading
    it is about to reboot the host — so it is asserted here that the counts are
    absent rather than zeroed, and that the body says which of the two 503s this is.

    Root CLAUDE.md §1: a null reading is "not measured", not `0`. This is that rule
    on the one path where the flattering answer is also the destructive one.
    """
    break_the_database(client)

    response = await client.get("/health/printing")
    body = response.json()

    assert response.status_code == 503
    assert body["status"] == "unknown"
    assert body["in_flight"] is None
    # Spelled out rather than left implied by the `is None` above. This is the
    # mutation worth catching, and it is one line away at all times.
    assert body["in_flight"] != {"assigned": 0, "dispatching": 0, "printing": 0}
    # A code, never prose (ADR-0012). The guard greps for it; a person reads it in a
    # log line and knows the reboot was deferred because nothing could be measured,
    # not because something was printing.
    assert body["code"] == "error.health.production_unreadable"


# ------------------------------------------------------------ what the body may carry


def carried_values(value: Any) -> list[Any]:
    """Every scalar anywhere in a response body, however deeply nested."""
    if isinstance(value, dict):
        return [found for item in value.values() for found in carried_values(item)]
    if isinstance(value, list):
        return [found for item in value for found in carried_values(item)]
    return [value]


async def test_the_body_carries_counts_and_nothing_else(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`/health/*` is unauthenticated, and that makes this a design constraint
    rather than an accident.

    The router's own docstring says these endpoints carry no farm data and that the
    storefront's edge must not forward them. So the shape is pinned: exactly two
    keys, exactly three counts, and every value in the body either one of the two
    known status words or a plain integer. A job id, a printer name, a due date, a
    duration or a price appearing here would fail — including the money case, which
    lives behind `VIEW_FINANCIALS` and must never ride along on a response any
    unauthenticated caller can read.
    """
    await stage(db_session, JobStatus.PRINTING, JobStatus.ASSIGNED)

    body = (await client.get("/health/printing")).json()

    assert set(body) == {"status", "in_flight"}
    assert set(body["in_flight"]) == {"assigned", "dispatching", "printing"}
    for value in carried_values(body):
        assert value in {"quiet", "in_flight"} or isinstance(value, int)


async def test_the_unreadable_body_carries_no_more_than_the_others(
    client: AsyncClient,
) -> None:
    """The failure path gets one extra key and no extra information.

    A `detail` string, an exception message or a database URL would all be natural
    things to add here while debugging, and all three would be leaked to an
    unauthenticated caller describing the shape of the deployment.
    """
    break_the_database(client)

    body = (await client.get("/health/printing")).json()

    assert set(body) == {"status", "in_flight", "code"}
