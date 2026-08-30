"""Reading back the rates an order was priced at.

ADR-0020 has always *held* — a rate edit changes the next quote and nothing
already sold, and `test_rate_snapshots.py` proves it at the unit level. What was
missing is anyone being able to look. The settings screen lets an owner change
seventeen pricing rates; a customer asks why a repeat order costs more than last
month's; the system holds both snapshots and could show neither.

The second case below is that customer's question, answered end to end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import a_shop, place, token_for


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async for http in a_shop(object_store, settings, clock, bus):
        yield http


async def test_the_desk_can_read_the_rates_an_order_was_priced_at(client: AsyncClient) -> None:
    """The capability that existed and had no way out of the database."""
    buyer = await token_for(client, "buyer@example.com")
    order = await place(client, buyer)
    boss = await token_for(client, "boss@example.com")

    response = await client.get(f"/orders/{order['id']}/rate-snapshot", headers=boss)

    assert response.status_code == 200
    body = response.json()
    # The id is the content hash the order already carries, so the two agree by
    # construction — which is what makes comparing two orders meaningful.
    assert body["id"] == order["rate_snapshot_id"]
    assert body["engine_version"] == order["engine_version"]
    # Keys and numbers, never labels: the client owns the words (ADR-0012), and
    # the settings screen already carries one for every rate in here.
    assert body["payload"]["margin_percent"] == "30"
    assert "schema_version" in body["payload"]


async def test_changing_a_rate_does_not_move_the_snapshot_an_order_already_carries(
    client: AsyncClient,
) -> None:
    """The customer's question, answered: *why does this cost more than last time?*

    Two orders either side of a margin change. The system has always known the
    answer — ADR-0020 pins the rates — and until this route existed it could not
    say it. The first order's snapshot still reports the margin it was sold at,
    and the two ids differ, which is the answer in one comparison.
    """
    buyer = await token_for(client, "buyer@example.com")
    boss = await token_for(client, "boss@example.com")
    before = await place(client, buyer)

    changed = await client.put(
        "/settings/pricing.margin_percent", json={"value": "45"}, headers=boss
    )
    assert changed.status_code == 200, changed.text

    after = await place(client, buyer)

    first = (await client.get(f"/orders/{before['id']}/rate-snapshot", headers=boss)).json()
    second = (await client.get(f"/orders/{after['id']}/rate-snapshot", headers=boss)).json()

    assert first["payload"]["margin_percent"] == "30"
    assert second["payload"]["margin_percent"] == "45"
    # Identical rates *are* the same snapshot, so different ids mean the rates
    # moved — and equal ids would have meant the difference was elsewhere.
    assert first["id"] != second["id"]


async def test_a_customer_cannot_read_the_rate_snapshot(client: AsyncClient) -> None:
    """A rate snapshot is the farm's cost structure, not the customer's receipt.

    `VIEW_FINANCIALS` is kept apart from every production permission (CLAUDE.md
    §1), and owning the order is not a way round it — the breakdown they agreed
    to is already on the order itself.
    """
    buyer = await token_for(client, "buyer@example.com")
    order = await place(client, buyer)

    response = await client.get(f"/orders/{order['id']}/rate-snapshot", headers=buyer)

    assert response.status_code == 403
    assert response.json()["code"] == "error.permission_denied"


async def test_an_unknown_order_is_a_404(client: AsyncClient) -> None:
    boss = await token_for(client, "boss@example.com")

    response = await client.get(f"/orders/{new_id()}/rate-snapshot", headers=boss)

    assert response.status_code == 404
    assert response.json()["code"] == "error.ordering.not_found"
