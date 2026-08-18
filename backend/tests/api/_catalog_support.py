"""Shared scaffolding for the catalogue's API tests.

Helpers only — no fixtures. Importing a fixture by name into another module makes
it shadow the parameter of every test that requests it, which reads to the linter
as a redefinition and to a reader as two things with one name. Each test module
declares its own `client` and `editor` from these.
"""

from __future__ import annotations

import struct
from collections.abc import AsyncIterator

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus

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


def a_cube(size: float = 40.0) -> bytes:
    """A watertight cube, so the upload measures as priceable."""
    s = size
    a, b, c, d = (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0)
    e, f, g, h = (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)
    triangles = [
        (a, c, b),
        (a, d, c),
        (e, f, g),
        (e, g, h),
        (a, b, f),
        (a, f, e),
        (b, c, g),
        (b, g, f),
        (c, d, h),
        (c, h, g),
        (d, a, e),
        (d, e, h),
    ]
    out = bytearray(b"test".ljust(80, b"\0")) + struct.pack("<I", len(triangles))
    for tri in triangles:
        out += struct.pack("<3f", 0.0, 0.0, 0.0)
        for vertex in tri:
            out += struct.pack("<3f", *vertex)
        out += struct.pack("<H", 0)
    return bytes(out)


async def sign_in(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus, role: Role
) -> None:
    """Create somebody with `role` and leave the client authenticated as them.

    A bearer token on the client's default headers rather than the session cookie:
    the cookie is `httpOnly` and same-origin, and `AsyncClient` talking to an ASGI
    transport is neither. Every other API test here does the same.
    """
    email = f"{role.value}@example.com"
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        await IdentityService(session, settings, clock, bus).create_user(
            CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
        )
        await session.commit()
    await database.dispose()
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"


async def upload(client: AsyncClient) -> str:
    response = await client.post(
        "/catalog/geometry", files={"file": ("cube.stl", a_cube(), "model/stl")}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["model_asset_id"])


def a_model(asset_id: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "slug": "test-bracket",
        "code": "TEST_B",
        "title": "Тестовый кронштейн",
        "category": "func",
        "model_asset_id": asset_id,
        "difficulty": 5,
        "materials": [
            {"code": "pla", "suitability": "excellent", "is_recommended": True},
            {"code": "petg"},
        ],
        "is_published": True,
    }
    return base | overrides


async def model_digest(client: AsyncClient, slug: str) -> str:
    """The mesh digest this model's jobs are matched on.

    A job records which *geometry* it printed, not which catalogue row sent it, so
    the history counters join on the content address. Storage is content-addressed,
    so hashing what `/model` serves gives exactly that.
    """
    import hashlib

    response = await client.get(f"/catalog/{slug}/model")
    return hashlib.sha256(response.content).hexdigest()
