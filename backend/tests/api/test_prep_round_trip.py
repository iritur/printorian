"""The prep round trip: model out, sliced plate back (ADR-0006, amended).

Split from `test_jobs_api.py` because it is a different responsibility — that file
covers who may do what to a job, this one covers the loop an engineer actually
walks. Both share the fixtures in `conftest`.
"""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.catalog import ModelLibrary
from printorian.contexts.catalog.plate_file import SLICE_INFO
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
            ("eng@example.com", Role.ENGINEER),
            ("op@example.com", Role.OPERATOR),
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
    """A job with no stored model — the case prep cannot serve."""
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


async def a_job_with_a_model(
    settings: Settings, clock: FixedClock, bus: EventBus, store: InMemoryObjectStore
) -> tuple[str, bytes]:
    """A job whose geometry the farm actually holds.

    Without a stored model there is nothing to hand a slicer, which is the whole
    reason prep was blocked before model storage existed.
    """

    geometry = a_binary_stl()
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        asset = await ModelLibrary(session, store, clock).ingest(geometry, filename="cube.stl")
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
                model_asset_id=asset.id,
            )
        )
        await session.commit()
    await database.dispose()
    return str(job.id), geometry


def a_binary_stl(size: float = 40.0) -> bytes:
    """A real cube. `ingest` measures the mesh, so a token string will not do."""

    corner = {
        "000": (0, 0, 0),
        "100": (size, 0, 0),
        "110": (size, size, 0),
        "010": (0, size, 0),
        "001": (0, 0, size),
        "101": (size, 0, size),
        "111": (size, size, size),
        "011": (0, size, size),
    }
    faces = [
        ("000", "110", "100"),
        ("000", "010", "110"),
        ("001", "101", "111"),
        ("001", "111", "011"),
        ("000", "100", "101"),
        ("000", "101", "001"),
        ("010", "011", "111"),
        ("010", "111", "110"),
        ("000", "001", "011"),
        ("000", "011", "010"),
        ("100", "110", "111"),
        ("100", "111", "101"),
    ]
    out = bytearray(bytes(80) + struct.pack("<I", len(faces)))
    for face in faces:
        out += struct.pack("<3f", 0, 0, 0)
        for name in face:
            out += struct.pack("<3f", *corner[name])
        out += struct.pack("<H", 0)
    return bytes(out)


def a_sliced_3mf(prediction_seconds: int = 4521) -> bytes:
    """A Bambu plate as the slicer writes it — a zip whose metadata is XML."""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <config>
      <plate>
        <metadata key="prediction" value="{prediction_seconds}"/>
        <filament id="1" used_g="17.30"/>
      </plate>
    </config>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(SLICE_INFO, xml)
    return buffer.getvalue()


async def test_the_engineer_downloads_the_model_under_its_own_name(
    client: AsyncClient,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> None:
    """Half of the loop. Served by name, because a directory of digests is unusable."""
    job_id, geometry = await a_job_with_a_model(settings, clock, bus, object_store)
    eng = await auth(client, "eng@example.com")

    response = await client.get(f"/jobs/{job_id}/model", headers=eng)

    assert response.status_code == 200
    assert response.content == geometry
    assert "cube.stl" in response.headers["content-disposition"]


async def test_a_job_with_no_stored_model_says_so(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """A 404 naming the missing record, not a zero-byte download."""
    job_id = await make_job(settings, clock, bus)
    eng = await auth(client, "eng@example.com")

    response = await client.get(f"/jobs/{job_id}/model", headers=eng)

    assert response.status_code == 404
    assert response.json()["code"] == "error.catalog.model_not_found"


async def test_uploading_a_plate_reads_its_numbers_and_clears_the_queue(
    client: AsyncClient,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> None:
    """The other half: the numbers come from the file, never from a form."""
    job_id, _ = await a_job_with_a_model(settings, clock, bus, object_store)
    eng = await auth(client, "eng@example.com")

    response = await client.post(
        f"/jobs/{job_id}/plate/file",
        files={"plate": ("cube.3mf", a_sliced_3mf(), "application/octet-stream")},
        headers=eng,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert (await client.get("/jobs/prep-queue", headers=eng)).json() == []


async def test_a_plate_that_does_not_state_its_numbers_is_refused(
    client: AsyncClient,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> None:
    """An invented print time would be repriced against as truth (ADR-0013).

    Refusing sends the engineer to the by-hand endpoint, which is honest about
    where the number came from.
    """
    job_id, _ = await a_job_with_a_model(settings, clock, bus, object_store)
    eng = await auth(client, "eng@example.com")

    response = await client.post(
        f"/jobs/{job_id}/plate/file",
        files={"plate": ("mystery.gcode", b"; generated by something\n", "text/plain")},
        headers=eng,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "error.catalog.plate_not_parsed"
    # Still in the queue: nothing was recorded, so there is still work to do.
    assert len((await client.get("/jobs/prep-queue", headers=eng)).json()) == 1


async def test_only_an_engineer_may_upload_a_plate(
    client: AsyncClient,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> None:
    job_id, _ = await a_job_with_a_model(settings, clock, bus, object_store)
    op = await auth(client, "op@example.com")

    refused = await client.post(
        f"/jobs/{job_id}/plate/file",
        files={"plate": ("cube.3mf", a_sliced_3mf(), "application/octet-stream")},
        headers=op,
    )

    assert refused.status_code == 403
