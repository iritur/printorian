"""Placing an order to a zone, and the tariff that gets frozen with it.

The irreversible path, which is the one that most needs a test: an order is sold
at a tariff, the owner then re-draws the zone table, and the order must not move.
A settings row is exactly how that would now happen, so ADR-0020 is asserted here
rather than assumed from the fact that `rates_to_dict` iterates its own fields.

The second half is the invariant `POST /orders/reprice` exists for: the figure the
checkout shows for a postcode is the figure `POST /orders` charges for it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import a_shop, an_order_payload, token_for

#: Moscow charges the same for a keyring and a crate; the central region charges
#: by mass. Two rows are enough to prove both halves of a tariff.
MOSCOW = {
    "code": "msk",
    "base": "400",
    "per_kg": "0",
    "transit_days": 1,
    "postcode_prefixes": ["101", "1"],
    "enabled": True,
}
CENTRAL = {
    "code": "cfo",
    "base": "550",
    "per_kg": "60",
    "transit_days": 3,
    "postcode_prefixes": ["3"],
    "enabled": True,
}

#: Тула — inside «ЦФО», so priced by mass.
TULA = "300000"
#: Владивосток — outside every zone the farm has drawn.
VLADIVOSTOK = "690000"


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


async def draw_zones(client: AsyncClient, zones: list[dict[str, Any]]) -> None:
    """Set the tariff the way the owner does — through the settings screen."""
    auth = await token_for(client, "boss@example.com")
    response = await client.put("/settings/logistics.zones", json={"value": zones}, headers=auth)
    assert response.status_code == 200, response.text


def courier_to(postcode: str) -> dict[str, Any]:
    payload = an_order_payload()
    payload["delivery"] = {
        "method": "courier",
        "city": "Тула",
        "postcode": postcode,
        "address": "Ленина 1",
    }
    return payload


def line_amounts(breakdown: dict[str, Any]) -> dict[str, str]:
    return {line["code"]: line["amount"] for line in breakdown["lines"]}


# ------------------------------------------------------------ pricing an order


async def test_an_order_to_a_zoned_postcode_is_priced_by_the_tariff(
    client: AsyncClient,
) -> None:
    await draw_zones(client, [MOSCOW, CENTRAL])
    auth = await token_for(client, "buyer@example.com")

    order = (await client.post("/orders", json=courier_to(TULA), headers=auth)).json()

    amounts = line_amounts(order["price_breakdown"])
    assert Decimal(amounts["logistics.shipping"]) == Decimal(550)
    # Two copies at 90 g is 0.18 kg, at 60 ₽/kg.
    assert Decimal(amounts["logistics.shipping_weight"]) == Decimal("10.80")


async def test_a_flat_rate_zone_adds_no_weight_line(client: AsyncClient) -> None:
    await draw_zones(client, [MOSCOW, CENTRAL])
    auth = await token_for(client, "buyer@example.com")

    order = (await client.post("/orders", json=courier_to("101000"), headers=auth)).json()

    amounts = line_amounts(order["price_breakdown"])
    assert Decimal(amounts["logistics.shipping"]) == Decimal(400)
    assert "logistics.shipping_weight" not in amounts


async def test_a_postcode_in_no_zone_is_quoted_the_flat_rate_not_refused(
    client: AsyncClient,
) -> None:
    """The customer is not blocked because the owner has not drawn their region yet.

    And it is not priced as the nearest zone either: the farm never set a rate for
    Vladivostok, so the one it did set — the flat rate — is what it charges.
    """
    await draw_zones(client, [MOSCOW, CENTRAL])
    auth = await token_for(client, "buyer@example.com")

    response = await client.post("/orders", json=courier_to(VLADIVOSTOK), headers=auth)

    assert response.status_code == 201
    amounts = line_amounts(response.json()["price_breakdown"])
    assert Decimal(amounts["logistics.shipping"]) == Decimal(400)
    assert "logistics.shipping_weight" not in amounts


# ------------------------------------------------------------ the pinned tariff


async def test_the_tariff_used_is_archived_with_the_order(client: AsyncClient) -> None:
    """The whole zone table, in `rate_snapshots.payload`, not a reference to it."""
    await draw_zones(client, [MOSCOW, CENTRAL])
    buyer = await token_for(client, "buyer@example.com")
    order = (await client.post("/orders", json=courier_to(TULA), headers=buyer)).json()

    boss = await token_for(client, "boss@example.com")
    snapshot = (await client.get(f"/orders/{order['id']}/rate-snapshot", headers=boss)).json()

    assert snapshot["payload"]["zones"] == [MOSCOW, CENTRAL]


async def test_re_drawing_the_zones_does_not_move_an_order_already_sold(
    client: AsyncClient,
) -> None:
    """ADR-0020, on the surface that would now break it.

    A tariff is a settings row, so the owner can change it between two quotes.
    The order sold under the old one keeps its breakdown *and* its archived
    tariff, while the next order prices at the new figure — the second half
    matters too, because an assertion that nothing changed is worthless if the
    edit did nothing.
    """
    await draw_zones(client, [MOSCOW, CENTRAL])
    buyer = await token_for(client, "buyer@example.com")
    sold = (await client.post("/orders", json=courier_to(TULA), headers=buyer)).json()

    dearer = {**CENTRAL, "base": "900", "per_kg": "120"}
    await draw_zones(client, [MOSCOW, dearer])

    reread = (await client.get(f"/orders/{sold['id']}", headers=buyer)).json()
    assert reread["total"] == sold["total"]
    assert line_amounts(reread["price_breakdown"]) == line_amounts(sold["price_breakdown"])
    assert reread["rate_snapshot_id"] == sold["rate_snapshot_id"]

    boss = await token_for(client, "boss@example.com")
    snapshot = (await client.get(f"/orders/{sold['id']}/rate-snapshot", headers=boss)).json()
    assert snapshot["payload"]["zones"] == [MOSCOW, CENTRAL]

    later = (await client.post("/orders", json=courier_to(TULA), headers=buyer)).json()
    assert Decimal(line_amounts(later["price_breakdown"])["logistics.shipping"]) == Decimal(900)
    assert later["rate_snapshot_id"] != sold["rate_snapshot_id"]


# ------------------------------------------------------------ the reprice invariant


async def test_reprice_answers_without_an_address_and_sharpens_with_one(
    client: AsyncClient,
) -> None:
    """The pre-address figure survives; the postcode only makes it more exact."""
    await draw_zones(client, [MOSCOW, CENTRAL])
    body: dict[str, Any] = {"method": "courier", "lines": an_order_payload()["lines"]}

    blind = (await client.post("/orders/reprice", json=body)).json()
    zoned = (await client.post("/orders/reprice", json={**body, "postcode": TULA})).json()

    assert Decimal(line_amounts(blind["breakdown"])["logistics.shipping"]) == Decimal(400)
    assert "logistics.shipping_weight" not in line_amounts(blind["breakdown"])
    assert Decimal(line_amounts(zoned["breakdown"])["logistics.shipping"]) == Decimal(550)


async def test_the_repriced_figure_is_the_figure_charged(client: AsyncClient) -> None:
    """The one thing a re-price must never get wrong, now that shipping varies."""
    await draw_zones(client, [MOSCOW, CENTRAL])
    auth = await token_for(client, "buyer@example.com")

    quoted = (
        await client.post(
            "/orders/reprice",
            json={"method": "courier", "postcode": TULA, "lines": an_order_payload()["lines"]},
            headers=auth,
        )
    ).json()
    order = (await client.post("/orders", json=courier_to(TULA), headers=auth)).json()

    assert Decimal(quoted["breakdown"]["total"]) == Decimal(str(order["total"]))
    assert line_amounts(quoted["breakdown"]) == line_amounts(order["price_breakdown"])
