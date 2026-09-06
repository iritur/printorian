"""The store over HTTP: the two gates, the 404, and the money that must not be here.

The permission split is exercised as an operator and as a manager rather than
asserted from the decorator — a dependency can be attached to the wrong router and
still look right in the source. The money assertion is the one that would be
easiest to leave out and is the reason this file exists at all: the kit draws
«Стоимость остатков» beside the fill figure, and the same leak has reached the
shop floor here before through `JobEvent.details`.
"""

from __future__ import annotations

import json
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

#: Anything that would mean money. Asserted against the whole serialized body
#: rather than against a list of known fields, because the failure this guards is
#: a *new* field arriving — a check that enumerates today's keys cannot see one.
MONEY_WORDS = ("price", "cost", "value", "rub", "₽", "money", "total_value")

#: The one spool every test in this file works with. A module constant rather than
#: a fixture, because the client fixture already takes the five dependencies ruff
#: allows positionally and a sixth would be a lint failure about the wrong thing.
#: Each test gets a truncated database, so nothing carries between them.
LOT_ID = new_id()


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
        for email, role in (
            ("boss@example.com", Role.MANAGER),
            ("op@example.com", Role.OPERATOR),
            ("buyer@example.com", Role.CUSTOMER),
        ):
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
                # A purchase price on the row, so the money guard below is asserting
                # about a lot that *has* one rather than about an empty column.
                purchase_price=Decimal("1850.00"),
            )
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


async def declare(client: AsyncClient, headers: dict[str, str], address: str) -> None:
    await client.post("/store/zones", json={"code": "A", "name": "Зона A"}, headers=headers)
    response = await client.post(
        "/store/cells", json={"zone_code": "A", "address": address}, headers=headers
    )
    assert response.status_code == 201, response.text


async def test_the_cell_map_is_refused_without_view_production(client: AsyncClient) -> None:
    """A customer has no business knowing where the farm keeps its filament."""
    response = await client.get("/store/cells", headers=await auth(client, "buyer@example.com"))
    assert response.status_code == 403

    # And an operator, who has `VIEW_PRODUCTION`, does get it — otherwise the test
    # above would pass just as well against a router nobody can reach.
    allowed = await client.get("/store/cells", headers=await auth(client, "op@example.com"))
    assert allowed.status_code == 200


async def test_placing_a_lot_is_refused_without_manage_inventory(client: AsyncClient) -> None:
    """Finding a spool and deciding where it goes are different jobs.

    Exercised as the operator rather than read off the decorator: the read gate is
    on the router and the write gate is per route, and the whole point of that
    arrangement is that an operator gets past the first and not the second.
    """
    manager = await auth(client, "boss@example.com")
    await declare(client, manager, "A1-1")

    refused = await client.post(
        f"/store/lots/{LOT_ID}/place",
        json={"address": "A1-1"},
        headers=await auth(client, "op@example.com"),
    )
    assert refused.status_code == 403

    allowed = await client.post(
        f"/store/lots/{LOT_ID}/place", json={"address": "A1-1"}, headers=manager
    )
    assert allowed.status_code == 200
    assert allowed.json()["cell"] == "A1-1"


async def test_an_unknown_cell_address_404s_rather_than_answering_an_empty_map(
    client: AsyncClient,
) -> None:
    """CLAUDE.md §1, third bullet, verbatim.

    An empty detail would read as "this cell holds nothing", which is a claim about
    a cell the farm does not have — and the person reading it is standing in an
    aisle looking for it.
    """
    headers = await auth(client, "op@example.com")
    response = await client.get("/store/cells/Z9-9", headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "error.inventory.cell_not_found"


async def test_the_cell_map_carries_no_money(client: AsyncClient) -> None:
    """Not one rouble anywhere on a response `VIEW_PRODUCTION` opens.

    The lot in the fixture carries a `purchase_price`, so this is asserting that
    the field is *not serialized* rather than that the column happens to be empty.
    Scanning the whole body catches a field added later, which the leak this guards
    against actually was.
    """
    manager = await auth(client, "boss@example.com")
    await declare(client, manager, "A1-1")
    await client.post(f"/store/lots/{LOT_ID}/place", json={"address": "A1-1"}, headers=manager)

    headers = await auth(client, "op@example.com")
    bodies = [
        (await client.get("/store/cells", headers=headers)).text,
        (await client.get("/store/cells/A1-1", headers=headers)).text,
        (await client.get("/store/movements", headers=headers)).text,
    ]
    for body in bodies:
        lowered = body.lower()
        found = [word for word in MONEY_WORDS if word in lowered]
        assert not found, f"money reached a production response through {found}: {body}"


async def test_a_write_off_appears_in_the_movements_read(client: AsyncClient) -> None:
    """The round trip, so the ledger is proved reachable rather than only written.

    A service-level test can pass while the route serves something else entirely;
    this is the assertion that the thing an operator can look at is the thing the
    write produced.
    """
    manager = await auth(client, "boss@example.com")
    response = await client.post(
        f"/store/lots/{LOT_ID}/write-off",
        json={"grams": "250.00", "note": "брак"},
        headers=manager,
    )
    assert response.status_code == 200
    assert Decimal(response.json()["remaining_grams"]) == Decimal("750.00")

    feed = await client.get(
        f"/store/movements?lot_id={LOT_ID}", headers=await auth(client, "op@example.com")
    )
    assert feed.status_code == 200
    rows = feed.json()
    assert [row["reason"] for row in rows] == ["stock.written_off"]
    assert Decimal(rows[0]["grams"]) == Decimal("250.00")
    assert Decimal(rows[0]["remaining_after"]) == Decimal("750.00")
    # The actor is recorded, because "who" is half of what a ledger is for.
    assert rows[0]["actor_id"] is not None
    # And the serialized row is JSON the console can render without a lookup table.
    assert json.loads(feed.text)[0]["sequence"] == 1
