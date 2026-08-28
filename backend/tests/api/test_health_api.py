"""The three health endpoints, over HTTP.

These lived in `test_auth_api.py`, which is a file about the authorization
boundary and was two dozen lines from the 400-line gate. They are here because
readiness now reports six things rather than two, and a check nobody asserts on is
a check that can be deleted by accident.

What matters most is the last case: `assignment_records` is reported *degraded*
rather than *failed*. A table that has outgrown its shape serves requests perfectly
well, so taking the process out of rotation over it would turn a schema chore into
an outage — the same argument the relay and WAL archiving already make in
`api/routers/health.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.fleet.models import Printer
from printorian.contexts.production import growth
from printorian.core import pagination
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app


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


async def test_health_is_open(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_each_dependency(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


async def test_readiness_names_the_watched_tables_separately(client: AsyncClient) -> None:
    """Each condition answers for itself, so an alert names its own cause.

    `wal_archiving` is deliberately not asserted on: whether the development stack
    has a working `archive_command` is a property of the machine the suite runs on,
    not of this code.
    """
    checks = (await client.get("/health/ready")).json()["checks"]
    assert checks["telemetry_partitions"] == "ok"
    assert checks["assignment_records"] == "ok"
    assert checks["printers_listing"] == "ok"
    assert checks["materials_listing"] == "ok"


async def test_a_table_past_its_partition_trigger_degrades_readiness(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the check, exercised through the real reading.

    The trigger is moved rather than the measurement stubbed: a stub would go on
    passing after `table_size` broke, which is exactly when this check has to work.
    Ten million rows cannot be arranged in a test, and 20 GiB cannot be written to
    a CI disk, so the threshold is what moves.

    One byte rather than zero, deliberately. An empty `assignment_records` still
    has its indexes, so its `pg_total_relation_size` is tens of kilobytes and this
    passes — while a reading that had silently become "nothing measured, call it
    zero" would not, which is the mutation worth catching here.
    """
    monkeypatch.setattr(growth, "BYTE_TRIGGER", 1)

    response = await client.get("/health/ready")

    assert response.json()["checks"]["assignment_records"] == "degraded"
    assert response.json()["status"] == "degraded"
    # Not 503. A table that wants partitioning still serves every request, and a
    # readiness failure here would take the API out of rotation over a schema
    # chore — the outage would be the check, not the condition.
    assert response.status_code == 200


async def test_a_listing_past_its_paging_trigger_degrades_readiness(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two listings that still return everything, watched rather than remembered.

    Real rows and a moved threshold, for the same reason as the case above: a
    stubbed reading goes on passing after the count breaks, which is precisely when
    this has to work. One printer with the trigger at one is enough to prove the
    count reads the table — a check that had quietly become "no rows, call it fine"
    fails here, and that is the mutation worth catching.

    `materials_listing` stays `ok` in the same response, which is the other half:
    the two readings are separate, so an alert names the listing that grew.
    """
    monkeypatch.setattr(pagination, "UNPAGINATED_ROW_TRIGGER", 1)
    db_session.add(Printer(name="The one that tips it over"))
    await db_session.commit()

    response = await client.get("/health/ready")
    checks = response.json()["checks"]

    assert checks["printers_listing"] == "degraded"
    assert checks["materials_listing"] == "ok"
    assert response.json()["status"] == "degraded"
    # Not 503, for the reason the case above gives: an unpaged listing serves every
    # request it is asked for. Taking the API out of rotation over a response that
    # has grown would make the check the outage.
    assert response.status_code == 200
