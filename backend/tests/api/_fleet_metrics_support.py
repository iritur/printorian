"""The world a metrics test needs: a database, three roles, and a closed hour.

Helpers rather than fixtures, the way the journal and catalogue suites do it —
importing a fixture by name shadows the parameter of every test that takes it,
which reads as a redefinition to a linter.

The one thing here that is not boilerplate is `LAST_CLOSED`. `FROZEN_NOW` is 09:00
exactly, so the open hour begins there and 08:00 is the newest hour the sweep could
ever have written. Every window in these tests is cut against that rather than
against `now`, because the routes clamp to it and a test that ignored the clamp
would be asserting on a bucket the endpoint refuses to serve.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import FROZEN_NOW, wire_app

PASSWORD = "correct-horse-battery"

#: The newest hour anything can have been summarised for. See the module docstring.
LAST_CLOSED = FROZEN_NOW - timedelta(hours=1)


class MetricsDatabase:
    """The session factory the app and the tests share.

    Named for what it is rather than ``TestDatabase``: pytest tries to *collect*
    any class whose name starts with "Test" and warns that it cannot, because this
    one has a constructor.
    """

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


async def signed_in_app(
    database: MetricsDatabase,
    *,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> AsyncClient:
    """The app with an owner, an operator and a customer already registered.

    All three, in every case, because the interesting assertions here are about who
    is refused — and a suite that created only the role it needed would make each
    permission test quietly depend on which fixture ran.
    """
    app = create_app(settings)
    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (
            ("boss@example.com", Role.OWNER),
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
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def register(client: AsyncClient, headers: dict[str, str], name: str = "P-01") -> EntityId:
    created = await client.post(
        "/printers", json={"name": name, "connection_mode": "manual"}, headers=headers
    )
    assert created.status_code == 201, created.text
    return EntityId(created.json()["id"])


def since(hours: int) -> str:
    """The start of a window ``hours`` buckets wide ending at the open hour."""
    return (LAST_CLOSED - timedelta(hours=hours - 1)).isoformat()


__all__ = [
    "LAST_CLOSED",
    "PASSWORD",
    "MetricsDatabase",
    "auth",
    "register",
    "signed_in_app",
    "since",
]
