"""The purchasing desk over HTTP: the money split, the 404, and one transaction.

Four rules make this screen honest rather than merely present.

**The board carries no money for anyone.** Asserted on the response *schema's*
own fields rather than on one fixture's happening not to contain a price, so it
fails the moment somebody adds a ruble to it — which is the way a money field
actually arrives, on a quiet afternoon, in a field named `total`.

**Prices are refused whole, not blanked.** A null already means "not measured"
(ADR-0007), and reusing it for "not permitted" makes the two indistinguishable.
`GET /jobs/variances` refuses whole for the same reason.

**An unknown order is a 404.** An all-null purchase order reads as "this order
bought nothing", which is a claim about a thing that does not exist.

**A delivery is one transaction.** The last case here drives a two-line delivery
whose second line cannot be received, through the real request-scoped session,
and then reads the database back: neither the first line's lot nor its receipt
may survive. That is the case a unit test cannot make, because the rollback lives
in `get_db`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.api.deps import get_current_actor
from printorian.contexts.identity import Actor, CreateUser, IdentityService, Permission, Role
from printorian.contexts.inventory import (
    CreateMaterialLot,
    CreateMaterialSpec,
    InventoryService,
)
from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.procurement import PurchaseOrderView, PurchasingBoard
from printorian.contexts.procurement.models import PurchaseReceipt
from printorian.contexts.settings import SettingsService
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app

PASSWORD = "correct-horse-battery"
MATERIAL = "PLA-BLACK"

#: Every field name on the two responses a production role may read. Money must
#: not appear in any of them, at any depth.
_MONEY_WORDS = ("price", "cost", "total_rub", "amount", "rub", "sum", "spend", "budget")


class _TestDatabase:
    """Stands in for `core.db.Database`, per the idiom the API tests use.

    The rollback in `session` is not incidental here: it is the thing
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


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[_TestDatabase]:
    database = _TestDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    database: _TestDatabase,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)

    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (
            ("boss@example.com", Role.MANAGER),
            # No `MANAGE_INVENTORY` at all: the floor does not decide what the
            # farm buys.
            ("floor@example.com", Role.OPERATOR),
        ):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        await InventoryService(session).create_spec(
            CreateMaterialSpec(code=MATERIAL, name="PLA Black", family="PLA")
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
        http.app = app  # type: ignore[attr-defined]
        yield http


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


def _money_fields(model: type) -> set[str]:
    """Field names on a response model that look like money, at any depth."""
    found: set[str] = set()
    for name, field in model.model_fields.items():  # type: ignore[attr-defined]
        if any(word in name for word in _MONEY_WORDS):
            found.add(f"{model.__name__}.{name}")
        for nested in (field.annotation, *getattr(field.annotation, "__args__", ())):
            if hasattr(nested, "model_fields") and nested is not model:
                found |= _money_fields(nested)
    return found


def test_the_board_carries_no_money_for_anyone() -> None:
    """Asserted on the schema, not on a fixture.

    A board built from an empty farm contains no prices whatever the code does, so
    a test reading the JSON would pass against a response type that had grown a
    `total_cost`. This reads the type.
    """
    assert _money_fields(PurchasingBoard) == set()
    assert _money_fields(PurchaseOrderView) == set()


async def test_a_manager_sees_the_board_and_the_floor_does_not(client: AsyncClient) -> None:
    """`MANAGE_INVENTORY` is the gate on the whole router — the permission that
    decides what the farm buys, which is not a shift-floor call."""
    assert (await client.get("/purchasing/board", headers=await auth(client))).status_code == 200

    refused = await client.get("/purchasing/board", headers=await auth(client, "floor@example.com"))
    assert refused.status_code == 403
    assert refused.json()["code"] == "error.permission_denied"


async def test_costs_are_refused_without_view_financials(
    client: AsyncClient, settings: Settings
) -> None:
    """403 with a code, never a body with the money blanked.

    The actor is substituted rather than signed in, and that is worth reading:
    no role in `identity.policies` holds `MANAGE_INVENTORY` without also holding
    `VIEW_FINANCIALS`, so the caller this route's second gate exists for is one
    the farm cannot currently create. The gate is still the thing that keeps the
    split real the day such a role is added — drop `dependencies=[_MONEY]` from
    `/costs` and this test goes green with the prices in hand.
    """
    order = await an_order(client)
    app = client.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_current_actor] = lambda: Actor(
        user_id=new_id(),
        email="buyer@example.com",
        display_name="buyer",
        role=Role.MANAGER,
        locale="ru",
        permissions=frozenset({Permission.MANAGE_INVENTORY}),
    )
    try:
        response = await client.get(f"/purchasing/orders/{order['id']}/costs")
        receiving = await client.post(
            f"/purchasing/orders/{order['id']}/receive",
            json={"lines": [{"line_id": order["lines"][0]["id"], "quantity": "10"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["code"] == "error.permission_denied"
    assert response.json()["details"]["permission"] == "view_financials"
    # The body carries `unit_price_paid`, so writing money is gated like reading it.
    assert receiving.status_code == 403


async def test_an_unknown_purchase_order_404s_rather_than_answering_an_empty_order(
    client: AsyncClient,
) -> None:
    """An all-null order reads as "this order bought nothing" (CLAUDE.md §1)."""
    response = await client.get(f"/purchasing/orders/{new_id()}", headers=await auth(client))

    assert response.status_code == 404
    assert response.json()["code"] == "error.procurement.order_not_found"


async def test_the_board_is_not_swallowed_by_the_order_route(client: AsyncClient) -> None:
    """FastAPI matches in declaration order. `/board` written below
    `/orders/{po_id}` would still resolve — as an order whose id is "board" — and
    fail as a 422 naming a UUID nobody asked for. There is no warning."""
    response = await client.get("/purchasing/board", headers=await auth(client))

    assert response.status_code == 200
    assert "reorder" in response.json()


async def test_the_reorder_list_uses_the_farms_threshold_and_not_a_constant(
    client: AsyncClient, database: _TestDatabase, clock: FixedClock
) -> None:
    """Move `inventory.low_stock_grams` and the same shelf changes side.

    The first reader those four `inventory.*` settings have ever had. A screen
    with 400 baked into it would pass every pure test of the rule and ignore the
    farm's own number completely.
    """
    headers = await auth(client)
    async with database.session_factory() as session:
        await InventoryService(session).add_lot(
            CreateMaterialLot(spec_code=MATERIAL, initial_grams=Decimal(300))
        )
        await session.commit()

    # 300 g is above a threshold of 150 and below the shipped default of 400.
    async with database.session_factory() as session:
        await SettingsService(session, clock).set_value("inventory.low_stock_grams", 150, by=None)
        await session.commit()
    quiet = await client.get("/purchasing/board", headers=headers)

    async with database.session_factory() as session:
        await SettingsService(session, clock).set_value("inventory.low_stock_grams", 500, by=None)
        await session.commit()
    loud = await client.get("/purchasing/board", headers=headers)

    assert quiet.json()["reorder"] == []
    assert [row["item_code"] for row in loud.json()["reorder"]] == [MATERIAL]


async def test_a_reorder_row_says_not_measured_where_nothing_measures_consumption(
    client: AsyncClient, database: _TestDatabase
) -> None:
    """The ADR-0007 tripwire, at the screen.

    `design/purchasing.html` puts «1.2 месяца» in this column. Nothing decrements
    `material_lots.remaining_grams`, so the only honest answer is that the
    consequence was never measured — and the console draws an em dash.
    """
    async with database.session_factory() as session:
        await InventoryService(session).add_lot(
            CreateMaterialLot(spec_code=MATERIAL, initial_grams=Decimal(10))
        )
        await session.commit()

    board = (await client.get("/purchasing/board", headers=await auth(client))).json()

    (row,) = board["reorder"]
    assert row["consequence"]["kind"] == "not_measured"
    assert row["consequence"]["committed_grams"] is None
    assert "coverage_months" not in row["consequence"]


async def test_receiving_puts_the_delivery_on_the_shelf(
    client: AsyncClient, database: _TestDatabase
) -> None:
    order = await an_order(client)

    response = await client.post(
        f"/purchasing/orders/{order['id']}/receive",
        json={
            "lines": [
                {
                    "line_id": order["lines"][0]["id"],
                    "quantity": "1000",
                    "unit_price_paid": "1.80",
                    "lot_number": "B-4471",
                }
            ]
        },
        headers=await auth(client),
    )

    assert response.status_code == 200
    async with database.session_factory() as session:
        lot = await session.scalar(select(MaterialLot))
    assert lot is not None and lot.lot_number == "B-4471"


async def test_a_refused_delivery_leaves_nothing_behind(
    client: AsyncClient, database: _TestDatabase
) -> None:
    """One transaction, read back rather than trusting the status code.

    The second line is packaging, which this build cannot put into stock. The
    first line is a material and would otherwise have been written already — a lot
    and a receipt, flushed before the refusal. The request-scoped session rolls
    both back, and this reads the database to prove it rather than believing the
    status code.
    """
    headers = await auth(client)
    created = await client.post(
        "/purchasing/orders",
        json={
            "lines": [
                {"kind": "material", "item_code": MATERIAL, "quantity": "1000", "unit": "gram"},
                {"kind": "packaging", "item_code": "BOX-M", "quantity": "20", "unit": "piece"},
            ]
        },
        headers=headers,
    )
    order = created.json()
    for stage in ("approved", "paid"):
        await client.post(
            f"/purchasing/orders/{order['id']}/status", json={"to": stage}, headers=headers
        )

    refused = await client.post(
        f"/purchasing/orders/{order['id']}/receive",
        json={
            "lines": [
                {"line_id": order["lines"][0]["id"], "quantity": "1000"},
                {"line_id": order["lines"][1]["id"], "quantity": "20"},
            ]
        },
        headers=headers,
    )

    # 422, not 400: `api/errors.py` maps every `ValidationError` there, and this
    # refusal is one. The whole suite asserts 422 for a domain refusal — this line
    # said 400 only because it was written where it could not be run.
    assert refused.status_code == 422
    assert refused.json()["code"] == "error.procurement.class_not_receivable"
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MaterialLot)) == 0
        assert await session.scalar(select(func.count()).select_from(PurchaseReceipt)) == 0
