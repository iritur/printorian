"""`GET /purchasing/prices` — money, so refused whole to anyone without the permission.

The route is the second one in the purchasing module that carries rubles, and it
takes the gate `/costs` takes. The actor without `VIEW_FINANCIALS` is substituted
rather than signed in, for the reason `test_purchasing_api.py` gives: no role in
`identity.policies` holds `MANAGE_INVENTORY` without also holding the money
permission, so the caller this gate exists for is one the farm cannot currently
create — and the gate is still what keeps the split real the day such a role is
added.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.api.deps import get_current_actor
from printorian.contexts.identity import Actor, Permission, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.api._purchasing_support import PurchasingDatabase, an_order, auth, seed_desk
from tests.conftest import wire_app


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[PurchasingDatabase]:
    database = PurchasingDatabase(settings.database_url)
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
    database: PurchasingDatabase,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with database.session_factory() as session:
        await seed_desk(session, settings, clock, bus)

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


async def test_prices_are_refused_without_view_financials(client: AsyncClient) -> None:
    """403 with a code — never the same body with the figures blanked."""
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
        response = await client.get("/purchasing/prices")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["code"] == "error.permission_denied"
    assert response.json()["details"]["permission"] == "view_financials"


async def test_the_panel_is_picked_from_what_receiving_recorded(client: AsyncClient) -> None:
    """Two arrivals of the same filament through the real receiving route, the
    first at 1.80 and the second at 1.50: «было» 1.80, now 1.50, and the change
    is the ratio between them — nothing was typed into any price table."""
    headers = await auth(client)
    for price in ("1.80", "1.50"):
        order = await an_order(client)
        received = await client.post(
            f"/purchasing/orders/{order['id']}/receive",
            json={
                "lines": [
                    {
                        "line_id": order["lines"][0]["id"],
                        "quantity": "1000",
                        "unit_price_paid": price,
                    }
                ]
            },
            headers=headers,
        )
        assert received.status_code == 200, received.json()

    response = await client.get("/purchasing/prices", headers=headers)

    assert response.status_code == 200
    [position] = response.json()["positions"]
    assert position["item_code"] == "PLA-BLACK"
    assert Decimal(position["earliest"]) == Decimal("1.80")
    assert Decimal(position["latest"]) == Decimal("1.50")
    assert Decimal(position["change"]) == Decimal("-0.1667")
    assert position["priced_receipts"] == 2
    assert position["unpriced_receipts"] == 0


async def test_an_empty_window_is_an_empty_list_not_an_error(client: AsyncClient) -> None:
    response = await client.get("/purchasing/prices?days=1", headers=await auth(client))

    assert response.status_code == 200
    assert response.json()["positions"] == []
