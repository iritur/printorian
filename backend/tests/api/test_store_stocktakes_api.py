"""`/store/stocktakes` over HTTP: the gates, the round trip, and the money door.

The service tests prove the count; this proves the edge — that the three writes
are the manager's, that the value is refused whole to an operator rather than
served with roubles nulled, and that nothing on the floor's routes carries money.
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
from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app

PASSWORD = "correct-horse-battery"
LOT_ID = new_id()
MONEY_WORDS = ("price", "cost", "value", "rub", "₽", "money")


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
        session.add(
            MaterialLot(
                id=LOT_ID,
                spec_id=spec_id,
                label="PLA-STORE-001",
                initial_grams=Decimal(1000),
                remaining_grams=Decimal(1000),
                # Priced, so the value route has something to cost and the money
                # guard below is about a lot that *has* a price.
                purchase_price=Decimal("2000.00"),
            )
        )
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


async def shelve(client: AsyncClient, manager: dict[str, str]) -> None:
    await client.post("/store/zones", json={"code": "A", "name": "Зона A"}, headers=manager)
    await client.post("/store/cells", json={"zone_code": "A", "address": "A1-1"}, headers=manager)
    placed = await client.post(
        f"/store/lots/{LOT_ID}/place", json={"address": "A1-1"}, headers=manager
    )
    assert placed.status_code == 200, placed.text


async def test_the_round_trip_and_who_may_take_each_step(client: AsyncClient) -> None:
    manager = await auth(client, "boss@example.com")
    operator = await auth(client, "op@example.com")
    await shelve(client, manager)

    # Opening a count changes nothing yet, but closing one changes the book, so
    # the whole path is the manager's.
    assert (await client.post("/store/stocktakes", json={}, headers=operator)).status_code == 403

    opened = await client.post("/store/stocktakes", json={"zone_code": "A"}, headers=manager)
    assert opened.status_code == 201, opened.text
    stocktake = opened.json()
    assert stocktake["positions"] == 1
    assert stocktake["counted"] == 0
    [line] = stocktake["lines"]
    assert line["counted_grams"] is None  # not counted, not zero

    counted = await client.post(
        f"/store/stocktakes/{stocktake['id']}/lines/{LOT_ID}",
        json={"counted_grams": "750"},
        headers=manager,
    )
    assert counted.status_code == 200, counted.text
    assert counted.json()["short"] == 1

    closed = await client.post(f"/store/stocktakes/{stocktake['id']}/close", headers=manager)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert Decimal(closed.json()["lines"][0]["variance_grams"]) == Decimal(-250)

    # The book followed the count, through the ledger.
    detail = (await client.get("/store/cells/A1-1", headers=operator)).json()
    assert Decimal(detail["lots"][0]["remaining_grams"]) == Decimal(750)
    feed = (await client.get(f"/store/movements?lot_id={LOT_ID}", headers=operator)).json()
    assert feed[0]["reason"] == "stock.counted"
    assert feed[0]["note"] == stocktake["number"]

    history = await client.get("/store/stocktakes", headers=operator)
    assert history.status_code == 200
    assert [row["number"] for row in history.json()] == [stocktake["number"]]

    # No money on any route the floor reads.
    for body in (history.text, closed.text):
        lowered = body.lower()
        found = [word for word in MONEY_WORDS if word in lowered]
        assert not found, f"money reached a production response through {found}: {body}"


async def test_the_value_is_refused_whole_to_an_operator_and_costed_for_a_manager(
    client: AsyncClient,
) -> None:
    manager = await auth(client, "boss@example.com")
    await shelve(client, manager)
    stocktake = (await client.post("/store/stocktakes", json={}, headers=manager)).json()
    await client.post(
        f"/store/stocktakes/{stocktake['id']}/lines/{LOT_ID}",
        json={"counted_grams": "750"},
        headers=manager,
    )
    await client.post(f"/store/stocktakes/{stocktake['id']}/close", headers=manager)

    refused = await client.get(
        f"/store/stocktakes/{stocktake['id']}/value", headers=await auth(client, "op@example.com")
    )
    assert refused.status_code == 403

    value = await client.get(f"/store/stocktakes/{stocktake['id']}/value", headers=manager)
    assert value.status_code == 200, value.text
    # 250 g short of a 2000 ₽ spool that started at 1000 g.
    assert Decimal(value.json()["short_value"]) == Decimal("500.00")
    assert Decimal(value.json()["over_value"]) == Decimal("0")
    assert value.json()["unpriced_lines"] == 0


async def test_an_unknown_stocktake_is_a_404_not_an_empty_count(client: AsyncClient) -> None:
    response = await client.get(
        f"/store/stocktakes/{new_id()}", headers=await auth(client, "op@example.com")
    )
    assert response.status_code == 404
    assert response.json()["code"] == "error.inventory.stocktake_not_found"
