"""The settings endpoints, and the promise that makes them safe to use.

The promise is ADR-0020's: an owner may change what the farm charges, and no
order that has already been agreed moves. Without it a settings screen is a way to
retroactively re-price the business, and the last test here is the one that would
notice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import AsyncClient

from printorian.contexts.pricing import RateSnapshot
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import a_shop, place, token_for
from tests.unit.test_mesh_analysis import cube_triangles, to_binary_stl

MARGIN = "pricing.margin_percent"
CUBE = to_binary_stl(cube_triangles(40.0))  # a priceable 40mm cube
MIN_LEAD = "sla.min_lead_hours"


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async for shop in a_shop(object_store, settings, clock, bus):
        yield shop


async def owner(client: AsyncClient) -> dict[str, str]:
    return await token_for(client, "boss@example.com")


# ------------------------------------------------------------ who may look


async def test_the_settings_are_closed_to_anonymous_callers(client: AsyncClient) -> None:
    assert (await client.get("/settings")).status_code == 401


async def test_a_customer_may_not_read_the_farms_rates(client: AsyncClient) -> None:
    """`margin_percent` is the farm's markup. It is not a public figure."""
    auth = await token_for(client, "buyer@example.com")

    assert (await client.get("/settings", headers=auth)).status_code == 403


async def test_an_owner_may(client: AsyncClient) -> None:
    assert (await client.get("/settings", headers=await owner(client))).status_code == 200


# ------------------------------------------------------------ reading


async def test_the_listing_carries_every_rate_with_its_default(client: AsyncClient) -> None:
    """The whole catalogue, not only what somebody has changed.

    The screen draws a row per parameter either way, and a client that had to
    merge in the code defaults itself would be a second place for them to live.
    """
    body = (await client.get("/settings", headers=await owner(client))).json()
    rows = {row["key"]: row for row in body}

    assert rows[MARGIN]["default"] == str(RateSnapshot().margin_percent)
    assert rows[MARGIN]["value"] == rows[MARGIN]["default"]
    assert rows[MARGIN]["is_overridden"] is False


# ------------------------------------------------------------ writing


async def test_an_override_is_stored_and_read_back(client: AsyncClient) -> None:
    auth = await owner(client)

    saved = await client.put(f"/settings/{MARGIN}", json={"value": "44"}, headers=auth)

    assert saved.status_code == 200
    assert saved.json()["value"] == "44"
    assert saved.json()["is_overridden"] is True

    rows = {row["key"]: row for row in (await client.get("/settings", headers=auth)).json()}
    assert rows[MARGIN]["value"] == "44"


async def test_a_value_that_is_not_a_number_is_refused(client: AsyncClient) -> None:
    refused = await client.put(
        f"/settings/{MARGIN}", json={"value": "thirty"}, headers=await owner(client)
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "error.settings.not_a_number"


async def test_an_unknown_key_is_a_404(client: AsyncClient) -> None:
    refused = await client.put(
        "/settings/pricing.margin_pct", json={"value": "40"}, headers=await owner(client)
    )

    assert refused.status_code == 404


async def test_a_reset_returns_the_default(client: AsyncClient) -> None:
    auth = await owner(client)
    await client.put(f"/settings/{MARGIN}", json={"value": "44"}, headers=auth)

    reset = await client.delete(f"/settings/{MARGIN}", headers=auth)

    assert reset.status_code == 200
    assert reset.json()["value"] == str(RateSnapshot().margin_percent)
    assert reset.json()["is_overridden"] is False


# ------------------------------------------------------------ the audit


async def test_the_audit_names_who_changed_what(client: AsyncClient) -> None:
    """«Было · Стало», which is the question the screen exists to answer."""
    auth = await owner(client)
    await client.put(f"/settings/{MARGIN}", json={"value": "44"}, headers=auth)
    await client.put(f"/settings/{MARGIN}", json={"value": "46"}, headers=auth)

    history = (await client.get("/settings/history", headers=auth)).json()

    assert [(row["old_value"], row["new_value"]) for row in history] == [
        ("44", "46"),
        (None, "44"),
    ]
    assert history[0]["changed_by"] is not None


# ------------------------------------------------------------ the promise


async def test_a_new_rate_prices_the_next_quote(client: AsyncClient) -> None:
    """The whole point: the store reaches the engine at the read edge.

    Two identical orders either side of a margin change, and the second must cost
    more. Comparing them rather than asserting a figure keeps the arithmetic where
    it belongs — the engine's own tests — while still proving the number travelled,
    which `total > 0` would not have done.
    """
    buyer = await token_for(client, "buyer@example.com")
    at_default = Decimal(str((await place(client, buyer))["total"]))

    auth = await owner(client)
    await client.put(f"/settings/{MARGIN}", json={"value": "80"}, headers=auth)

    at_eighty = Decimal(str((await place(client, buyer))["total"]))

    assert at_eighty > at_default


async def test_changing_a_rate_does_not_reprice_an_order_already_placed(
    client: AsyncClient,
) -> None:
    """ADR-0020, and the reason a settings screen is safe to ship.

    The order pinned its rate snapshot when it was agreed. Raising the margin
    afterwards must leave that order's total exactly where the customer saw it —
    otherwise this feature is a way to re-price work retroactively, and the farm's
    own history stops being trustworthy.
    """
    buyer = await token_for(client, "buyer@example.com")
    order = await place(client, buyer)
    agreed = order["total"]

    auth = await owner(client)
    await client.put(f"/settings/{MARGIN}", json={"value": "95"}, headers=auth)

    again = (await client.get(f"/orders/{order['id']}", headers=buyer)).json()
    assert again["total"] == agreed


async def test_a_lead_time_setting_changes_the_next_quote(client: AsyncClient) -> None:
    """The promise parameters reach the quote's lead time, not just the table.

    The same shape as the rate test above: two identical quotes either side of a
    `sla.min_lead_hours` change, and the second must promise later — which is the
    whole point of moving the three constants out of `promise.py` and into the
    settings store.
    """
    buyer = await token_for(client, "buyer@example.com")
    files = {"model": ("cube.stl", CUBE, "model/stl")}
    data = {"material_code": "pla-black", "quantity": 1}

    at_default = (await client.post("/pricing/quote", files=files, data=data, headers=buyer)).json()
    default_lead = Decimal(str(at_default["model"]["promised_hours"]))

    auth = await owner(client)
    await client.put(f"/settings/{MIN_LEAD}", json={"value": "96"}, headers=auth)

    at_ninety_six = (
        await client.post("/pricing/quote", files=files, data=data, headers=buyer)
    ).json()

    assert Decimal(str(at_ninety_six["model"]["promised_hours"])) > default_lead


async def test_drop_telemetry_is_safe_on_an_empty_farm(client: AsyncClient) -> None:
    """The destructive op that cannot hurt: nothing summarised → nothing dropped."""
    auth = await owner(client)

    body = (await client.post("/settings/drop-telemetry", headers=auth)).json()

    assert body["dropped"] == 0


async def test_reset_rates_returns_to_defaults(client: AsyncClient) -> None:
    """«Сбросить тарифы» is a real endpoint, and it is safe: the next quote is at
    the code default, and nothing already sold moves."""
    auth = await owner(client)
    await client.put(f"/settings/{MARGIN}", json={"value": "80"}, headers=auth)

    body = (await client.post("/settings/reset-rates", headers=auth)).json()

    assert body["reset"] == 1
    rows = {row["key"]: row for row in (await client.get("/settings", headers=auth)).json()}
    assert rows[MARGIN]["value"] == rows[MARGIN]["default"]
    assert rows[MARGIN]["is_overridden"] is False
