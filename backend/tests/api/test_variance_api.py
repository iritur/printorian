"""Serving the variances the farm has been recording all along.

ADR-0013 makes estimate-vs-actual a *policy* rather than a statistic: a plate
outside the band holds the job instead of dispatching it. Every variance was
being written and none was being served, so the detection worked and the queue it
feeds was invisible.

The cases here are the four rules that make the read honest rather than merely
present: money is behind `VIEW_FINANCIALS`, an unknown order is a 404 and not an
empty grid, the in-band rows are served too, and the route is not swallowed by
`GET /jobs/{job_id}` — which is silent when it breaks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.production import CreateJob, ProductionService
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import ensure_order, wire_app

PASSWORD = "correct-horse-battery"


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

    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (
            ("boss@example.com", Role.OWNER),
            ("eng@example.com", Role.ENGINEER),
        ):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        await session.commit()

    wire_app(
        app,
        settings=settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http

    await database.dispose()


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def a_job(settings: Settings, clock: FixedClock, bus: EventBus) -> tuple[str, str]:
    """A paid order's work waiting for a plate, as ``(order_id, job_id)``.

    The order is real rather than a fabricated id: PostgreSQL enforces
    `print_jobs.order_id`, and the variance carries the order through to the desk.
    """
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        order_id = new_id()
        await ensure_order(session, order_id)
        job = await ProductionService(session, clock, bus).create_job(
            CreateJob(
                order_id=order_id,
                material_type="PLA",
                colors=["#FFFFFF"],
                width_mm=Decimal(40),
                depth_mm=Decimal(40),
                height_mm=Decimal(40),
                grams_required=Decimal(20),
                estimated_minutes=Decimal(60),
            )
        )
        await session.commit()
    await database.dispose()
    return str(order_id), str(job.id)


def a_plate(**overrides: object) -> dict[str, object]:
    return {
        "model_hash": "abc123",
        "model_name": "cube.stl",
        "scale": "1",
        "material_code": "pla-white",
        "printer_profile": "p1s-0.4-pla",
        "print_minutes": "64",
        "filament_grams": {"0": "17.3"},
        "filename": "cube.3mf",
        "slicer_name": "BambuStudio",
        "slicer_version": "1.9.5",
        "profile_version": "2026.1",
        **overrides,
    }


async def record_plate(
    client: AsyncClient, job_id: str, *, quoted: str, prepared: str
) -> AsyncClient:
    eng = await auth(client, "eng@example.com")
    await client.post(
        f"/jobs/{job_id}/plate",
        json=a_plate(),
        params={"quoted_cost": quoted, "prepared_cost": prepared},
        headers=eng,
    )
    return client


async def test_a_recorded_variance_is_served_to_the_desk(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The gap this issue names: the detection worked, the queue was invisible.

    Without the route this is a 404 — there is nothing to call, while the row it
    would have returned has been sitting in `estimate_variances` since the plate
    was attached.
    """
    order_id, job_id = await a_job(settings, clock, bus)
    await record_plate(client, job_id, quoted="1000", prepared="1400")

    response = await client.get(
        "/jobs/variances",
        params={"order_id": order_id},
        headers=await auth(client, "boss@example.com"),
    )

    assert response.status_code == 200
    [row] = response.json()
    assert row["within_tolerance"] is False
    assert row["quoted_cost"] == "1000.00"
    assert row["prepared_cost"] == "1400.00"
    assert row["job_id"] == job_id
    # The manufacturing pair, which is what the estimator is calibrated on: a
    # price is an estimate times a tariff, and only one of those is the
    # estimator's to get right.
    assert row["estimated_minutes"] == "60.00"
    assert row["prepared_minutes"] == "64.00"


async def test_the_variances_route_is_not_swallowed_by_the_job_id_route(
    client: AsyncClient,
) -> None:
    """FastAPI matches in declaration order, and gives no warning when it does not.

    Declared below `GET /jobs/{job_id}`, this path resolves as `job_id="variances"`
    and fails as a 422 about a UUID nobody asked for. Nothing else in the suite
    would notice.
    """
    response = await client.get("/jobs/variances", headers=await auth(client, "boss@example.com"))

    assert response.status_code == 200
    assert response.json() == []


async def test_an_engineer_cannot_read_what_a_plate_cost(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The split CLAUDE.md §1 keeps: money is not a production permission.

    The engineer who *records* the plate holds `PREPARE_PLATE` and
    `VIEW_PRODUCTION` and may not read what it cost. The route is refused whole
    rather than answered with the money fields blanked — a null means "not
    measured" (ADR-0007), and using it for "not permitted" would make the two
    indistinguishable to the client.
    """
    _order_id, job_id = await a_job(settings, clock, bus)
    await record_plate(client, job_id, quoted="1000", prepared="1400")

    response = await client.get("/jobs/variances", headers=await auth(client, "eng@example.com"))

    assert response.status_code == 403


async def test_an_unknown_order_is_a_404_rather_than_an_empty_grid(
    client: AsyncClient,
) -> None:
    """An empty list here reads as "this order had no variances", which is a claim.

    Root CLAUDE.md §1: an unknown id must 404. The farm has measured nothing about
    an order that does not exist, and must not answer as though it had.
    """
    response = await client.get(
        "/jobs/variances",
        params={"order_id": str(new_id())},
        headers=await auth(client, "boss@example.com"),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "error.ordering.not_found"


async def test_a_variance_inside_the_band_is_served_too(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The in-band rows are the calibration dataset, not noise.

    ADR-0013 records them deliberately, and ROADMAP Phase 6 calibrates the mesh
    estimator against them. A read that defaulted to the escalations only would
    look like a filter and quietly take that dataset with it — so `exceeded_only`
    is opt-in, and this proves both directions.
    """
    order_id, job_id = await a_job(settings, clock, bus)
    await record_plate(client, job_id, quoted="1000", prepared="1000")
    boss = await auth(client, "boss@example.com")

    everything = await client.get("/jobs/variances", params={"order_id": order_id}, headers=boss)
    exceeded = await client.get(
        "/jobs/variances",
        params={"order_id": order_id, "exceeded_only": "true"},
        headers=boss,
    )

    assert [row["within_tolerance"] for row in everything.json()] == [True]
    assert exceeded.json() == []
