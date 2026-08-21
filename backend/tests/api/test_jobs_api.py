"""The prep queue and job desk over HTTP.

The rules under test: only an engineer records a plate, only a manager releases a
price hold, and the wait list tells a customer-facing client the difference
between "waiting for a machine" and "waiting for a person".
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
            ("op@example.com", Role.OPERATOR),
            ("buyer@example.com", Role.CUSTOMER),
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


async def make_job(settings: Settings, clock: FixedClock, bus: EventBus) -> str:
    """A paid order's work, waiting for a plate."""
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        # PostgreSQL enforces `print_jobs.order_id`, and a job only ever comes
        # into being from an order — so the order is created here rather than
        # fabricated, which is what the old SQLite suite let us get away with.
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
    return str(job.id)


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


# ------------------------------------------------------------ permissions


async def test_the_prep_queue_is_closed_to_customers(client: AsyncClient) -> None:
    buyer = await auth(client, "buyer@example.com")
    assert (await client.get("/jobs/prep-queue", headers=buyer)).status_code == 403


async def test_an_operator_may_watch_but_not_record_a_plate(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Slicing is an engineering job (ADR-0006)."""
    job_id = await make_job(settings, clock, bus)
    op = await auth(client, "op@example.com")

    assert (await client.get("/jobs/prep-queue", headers=op)).status_code == 200
    refused = await client.post(f"/jobs/{job_id}/plate", json=a_plate(), headers=op)
    assert refused.status_code == 403


async def test_an_engineer_cannot_release_a_price_hold(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """What is being approved is money, not a machine."""
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")

    refused = await client.post(f"/jobs/{job_id}/release", headers=eng)
    assert refused.status_code == 403


# -------------------------------------------------------------- the queue


async def test_a_job_appears_in_the_prep_queue(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")

    body = (await client.get("/jobs/prep-queue", headers=eng)).json()

    assert [entry["id"] for entry in body] == [job_id]


async def test_recording_a_plate_takes_the_job_out_of_the_queue(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")

    recorded = await client.post(
        f"/jobs/{job_id}/plate",
        json=a_plate(),
        params={"quoted_cost": "1000", "prepared_cost": "1000"},
        headers=eng,
    )

    assert recorded.status_code == 200
    assert recorded.json()["status"] == "ready"
    assert (await client.get("/jobs/prep-queue", headers=eng)).json() == []


async def test_the_engineer_who_sliced_it_is_recorded_from_the_session(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Provenance a client can set is provenance that can be wrong (ADR-0006)."""
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")
    me = (await client.get("/auth/me", headers=eng)).json()

    await client.post(
        f"/jobs/{job_id}/plate",
        json=a_plate(sliced_by=str(new_id())),  # a client trying to attribute it elsewhere
        params={"quoted_cost": "1000", "prepared_cost": "1000"},
        headers=eng,
    )

    found = await client.get(
        "/jobs/plates/find",
        params={
            "model_hash": "abc123",
            "material_code": "pla-white",
            "printer_profile": "p1s-0.4-pla",
        },
        headers=eng,
    )
    assert found.json()["sliced_by"] == me["user_id"]


async def test_a_configuration_sliced_once_is_found_again(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The lookup the console makes before opening a slicer."""
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")
    await client.post(
        f"/jobs/{job_id}/plate",
        json=a_plate(),
        params={"quoted_cost": "1000", "prepared_cost": "1000"},
        headers=eng,
    )

    found = await client.get(
        "/jobs/plates/find",
        params={
            "model_hash": "abc123",
            "material_code": "pla-white",
            "printer_profile": "p1s-0.4-pla",
        },
        headers=eng,
    )

    assert found.status_code == 200
    assert found.json()["print_minutes"] == "64.00"


async def test_an_unsliced_configuration_is_a_clean_miss(client: AsyncClient) -> None:
    eng = await auth(client, "eng@example.com")

    response = await client.get(
        "/jobs/plates/find",
        params={
            "model_hash": "never-seen",
            "material_code": "pla-white",
            "printer_profile": "p1s-0.4-pla",
        },
        headers=eng,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "error.catalog.plate_not_found"


# ------------------------------------------------------- the variance band


async def test_a_plate_beyond_tolerance_holds_the_job(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """ADR-0013 over HTTP, with the tolerance from configuration."""
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")

    recorded = await client.post(
        f"/jobs/{job_id}/plate",
        json=a_plate(),
        params={"quoted_cost": "1000", "prepared_cost": "1400"},
        headers=eng,
    )

    assert recorded.json()["status"] == "on_hold"


async def test_a_manager_releases_the_hold(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")
    await client.post(
        f"/jobs/{job_id}/plate",
        json=a_plate(),
        params={"quoted_cost": "1000", "prepared_cost": "1400"},
        headers=eng,
    )

    boss = await auth(client, "boss@example.com")
    released = await client.post(f"/jobs/{job_id}/release", headers=boss)

    assert released.status_code == 200
    assert released.json()["status"] == "ready"


# ------------------------------------------------------------ the wait list


async def test_the_wait_list_is_empty_when_nothing_is_waiting(client: AsyncClient) -> None:
    op = await auth(client, "op@example.com")

    assert (await client.get("/jobs/wait-list", headers=op)).json() == []


async def test_a_job_carries_its_history(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    job_id = await make_job(settings, clock, bus)
    op = await auth(client, "op@example.com")

    body = (await client.get(f"/jobs/{job_id}", headers=op)).json()

    assert body["status"] == "pending"
    assert [event["to_status"] for event in body["events"]] == ["pending"]


async def test_decisions_are_readable_for_a_job(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Empty until a planning pass runs — but the endpoint answers, rather than
    404ing, because "nothing decided yet" is a real answer."""
    job_id = await make_job(settings, clock, bus)
    op = await auth(client, "op@example.com")

    response = await client.get(f"/jobs/{job_id}/decisions", headers=op)

    assert response.status_code == 200
    assert response.json() == []
