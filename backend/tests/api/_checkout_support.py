"""The world a checkout test needs: a database, a material, and three people.

Extracted so the ordering cases and the delivery cases can be separate files
without building the same fixture twice. They are separate because they answer
different questions — one is about who may see an order, the other about what a
delivery choice does to its price — and one file answering both grew past the
length the project holds itself to.

Helpers, not fixtures: each test module declares its own `client`, the way the
catalogue suites do. Importing a fixture by name shadows the parameter of every
test that takes it, which reads as a redefinition to every linter that looks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.inventory import CreateMaterialSpec, InventoryService
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore

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


async def a_shop(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
) -> AsyncIterator[AsyncClient]:
    """A running storefront with one material and three accounts.

    Two customers, because half of what these tests check is that one of them
    cannot see the other's orders, and an owner for the cases that need staff.
    """
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with database.session_factory() as session:
        await InventoryService(session).create_spec(
            CreateMaterialSpec(
                code="pla-black",
                name="PLA Matte Black",
                family="PLA",
                sell_price_per_gram=Decimal("2.40"),
            )
        )
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (
            ("buyer@example.com", Role.CUSTOMER),
            ("rival@example.com", Role.CUSTOMER),
            ("boss@example.com", Role.OWNER),
        ):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        await session.commit()

    app.state.settings = settings
    app.state.clock = clock
    app.state.event_bus = bus
    app.state.database = database
    app.state.object_store = object_store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http

    await database.dispose()


async def token_for(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def an_order_payload() -> dict[str, object]:
    return {
        "customer_email": "buyer@example.com",
        "promised_days": 5,
        "lines": [
            {
                "model_name": "bracket.stl",
                "material_code": "pla-black",
                "quantity": 2,
                "estimated_minutes": "180",
                "estimated_grams": "90",
                "colors": ["black"],
                "finishes": [],
            }
        ],
    }


async def place(client: AsyncClient, auth: dict[str, str]) -> dict[str, object]:
    response = await client.post("/orders", json=an_order_payload(), headers=auth)
    assert response.status_code == 201, response.text
    return response.json()
