"""Shared scaffolding for the purchasing desk's API tests.

Helpers only — no fixtures, for the reason `_catalog_support.py` gives: a fixture
imported by name shadows the parameter of every test that requests it. Each test
module declares its own `database` and `client` from these, and the two modules
that do are split by responsibility rather than by the line counter:
`test_purchasing_api.py` is the four rules that keep the screen honest (the money
split, the 404, one transaction), `test_purchasing_scorecard_api.py` is issue
#34's last clause — the supplier scorecard is computed, never typed in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.inventory import CreateMaterialSpec, InventoryService
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus

PASSWORD = "correct-horse-battery"
MATERIAL = "PLA-BLACK"


class PurchasingDatabase:
    """Stands in for `core.db.Database`, per the idiom the API tests use.

    The rollback in `session` is not incidental: it is the thing
    `test_a_refused_delivery_leaves_nothing_behind` is about.
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


async def seed_desk(
    session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """A buyer, somebody who is not one, and one material to buy.

    `floor@` holds no `MANAGE_INVENTORY` at all: the floor does not decide what
    the farm buys, and the gate tests need somebody the router refuses.
    """
    identity = IdentityService(session, settings, clock, bus)
    for email, role in (
        ("boss@example.com", Role.MANAGER),
        ("floor@example.com", Role.OPERATOR),
    ):
        await identity.create_user(
            CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
        )
    await InventoryService(session).create_spec(
        CreateMaterialSpec(code=MATERIAL, name="PLA Black", family="PLA")
    )
    await session.commit()


async def auth(client: AsyncClient, email: str = "boss@example.com") -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def an_order(client: AsyncClient, **line: Any) -> dict[str, Any]:
    """One paid order with one material line, as the buyer would raise it."""
    headers = await auth(client)
    body = {"kind": "material", "item_code": MATERIAL, "quantity": "1000", "unit": "gram", **line}
    created = await client.post("/purchasing/orders", json={"lines": [body]}, headers=headers)
    order = created.json()
    for stage in ("approved", "paid"):
        await client.post(
            f"/purchasing/orders/{order['id']}/status", json={"to": stage}, headers=headers
        )
    return dict(order)
