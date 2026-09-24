"""`/store/turnover` and `/store/dead-stock` over HTTP: one open to the floor, one not.

Turnover is grams and days and takes the router's `VIEW_PRODUCTION`; dead stock
carries a value per lot and sits behind `VIEW_FINANCIALS` on top of it — refused
whole to an operator, never served with the value nulled (a null already means
"not measured", and here it means "no price was recorded", which is a different
fact). The composition — the ledger read, the fold, the gate — is what only the
edge can prove.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.inventory import MOVED_MOUNTED, MOVED_RECEIVED, LocationKind
from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.contexts.inventory.placement import record_movement
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app

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
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = clock.now()
    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (("boss@example.com", Role.MANAGER), ("op@example.com", Role.OPERATOR)):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        spec_id = new_id()
        session.add(
            MaterialSpec(
                id=spec_id,
                code="PLA-STORE",
                name="PLA Store",
                family="PLA",
                sell_price_per_gram=Decimal("2.50"),
            )
        )
        await session.flush()
        # Two lots received a hundred days ago. One was mounted after four days;
        # the other still sits on the shelf, priced, with 600 g of 1000 left.
        turned = MaterialLot(
            id=new_id(),
            spec_id=spec_id,
            label="PLA-STORE-001",
            initial_grams=Decimal(1000),
            remaining_grams=Decimal(1000),
            # On a machine, as the mount below would have left it: a spool in an
            # AMS is not dead stock however long ago it was mounted.
            location_kind=LocationKind.PRINTER,
        )
        idle = MaterialLot(
            id=new_id(),
            spec_id=spec_id,
            label="PLA-STORE-002",
            initial_grams=Decimal(1000),
            remaining_grams=Decimal(600),
            purchase_price=Decimal("1800.00"),
        )
        session.add_all([turned, idle])
        await session.flush()
        for lot, reason, days in (
            (turned, MOVED_RECEIVED, 100),
            (turned, MOVED_MOUNTED, 96),
            (idle, MOVED_RECEIVED, 100),
        ):
            await record_movement(session, lot, reason=reason, at=now - timedelta(days=days))
            # Flushed one at a time: `next_sequence` reads the ledger for its rung.
            await session.flush()
        await session.commit()

    wire_app(
        app, settings=settings, clock=clock, bus=bus, database=database, object_store=object_store
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http
    await database.dispose()


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def test_turnover_is_open_to_the_floor_and_counts_the_shelf_beside_the_mean(
    client: AsyncClient,
) -> None:
    op = await auth(client, "op@example.com")

    response = await client.get("/store/turnover", params={"days": 120}, headers=op)

    assert response.status_code == 200, response.text
    [row] = response.json()["rows"]
    assert row["family"] == "PLA"
    assert row["turned"] == 1
    assert Decimal(row["mean_days_on_shelf"]) == Decimal("4.0")
    assert row["still_on_shelf"] == 1


async def test_dead_stock_is_refused_to_the_floor_and_costed_for_the_manager(
    client: AsyncClient,
) -> None:
    op = await auth(client, "op@example.com")
    boss = await auth(client, "boss@example.com")

    refused = await client.get("/store/dead-stock", headers=op)
    assert refused.status_code == 403
    assert refused.json()["details"]["permission"] == "view_financials"

    served = await client.get("/store/dead-stock", params={"idle_days": 60}, headers=boss)
    assert served.status_code == 200, served.text
    report = served.json()
    [lot] = report["lots"]
    assert lot["label"] == "PLA-STORE-002"
    assert Decimal(lot["value"]) == Decimal("1080.00")
    assert Decimal(report["total_grams"]) == Decimal(600)
    assert Decimal(report["total_value"]) == Decimal("1080.00")
    assert report["unpriced_lots"] == 0
