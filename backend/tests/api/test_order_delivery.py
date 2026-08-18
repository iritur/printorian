"""What a delivery choice does to an order.

Split from the checkout cases because it answers a different question: not who may
see an order, but what the farm charges for getting it to somebody. The rule under
all of these is that collection is the *absence* of a service rather than a
discount on one — the engine omits the shipping line rather than zeroing it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import a_shop, an_order_payload, place, token_for


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


async def test_collection_is_not_priced_as_a_delivery(client: AsyncClient) -> None:
    """The one rule the delivery panel exists to enforce.

    Collection means the farm never ships anything, so there is no shipping line —
    not a zero one. Before the panel existed the spec used the engine's default and
    every order was priced with delivery, including the ones the customer came and
    collected.
    """
    auth = await token_for(client, "buyer@example.com")
    payload = an_order_payload()
    payload["delivery"] = {"method": "pickup"}

    order = (await client.post("/orders", json=payload, headers=auth)).json()

    codes = [line["code"] for line in order["price_breakdown"]["lines"]]
    assert "logistics.shipping" not in codes


async def test_a_courier_order_carries_the_shipping_line(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    payload = an_order_payload()
    payload["delivery"] = {
        "method": "courier",
        "city": "Москва",
        "postcode": "101000",
        "address": "Тверская 1",
    }

    order = (await client.post("/orders", json=payload, headers=auth)).json()

    codes = [line["code"] for line in order["price_breakdown"]["lines"]]
    assert "logistics.shipping" in codes
    assert order["delivery_method"] == "courier"
    assert order["delivery_city"] == "Москва"


async def test_shipping_makes_the_order_dearer_than_collecting_it(
    client: AsyncClient,
) -> None:
    """Same parts, same everything — the difference is the delivery and only that."""
    auth = await token_for(client, "buyer@example.com")

    collected = an_order_payload()
    collected["delivery"] = {"method": "pickup"}
    shipped = an_order_payload()
    shipped["delivery"] = {"method": "courier", "city": "Москва", "address": "Тверская 1"}

    a = (await client.post("/orders", json=collected, headers=auth)).json()
    b = (await client.post("/orders", json=shipped, headers=auth)).json()

    assert Decimal(b["total"]) > Decimal(a["total"])


async def test_a_shipped_order_without_an_address_is_refused(client: AsyncClient) -> None:
    """Refused at the edge rather than accepted and discovered in packing.

    An order the farm cannot deliver is worse than one that never got placed: it is
    already paid for by the time anybody notices.
    """
    auth = await token_for(client, "buyer@example.com")
    payload = an_order_payload()
    payload["delivery"] = {"method": "courier", "city": "Москва"}

    response = await client.post("/orders", json=payload, headers=auth)

    assert response.status_code == 422, response.text
    # A code, not prose (ADR-0012) — the client picks the wording and the language.
    assert response.json()["code"] == "error.ordering.delivery_address_required"


async def test_collection_needs_no_address(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    payload = an_order_payload()
    payload["delivery"] = {"method": "pickup"}

    assert (await client.post("/orders", json=payload, headers=auth)).status_code == 201


async def test_an_order_that_says_nothing_about_delivery_is_a_collection(
    client: AsyncClient,
) -> None:
    """The safe default: promising to ship somewhere nobody named is the unsafe one."""
    auth = await token_for(client, "buyer@example.com")

    order = await place(client, auth)

    assert order["delivery_method"] == "pickup"
    assert order["notify_on_progress"] is True


# ------------------------------------------------------------- re-pricing


def a_reprice(method: str) -> dict[str, object]:
    """The checkout asking what a delivery choice costs, before committing."""
    return {"method": method, "lines": an_order_payload()["lines"]}


async def test_repricing_needs_no_address(client: AsyncClient) -> None:
    """The rate is flat, so pricing a courier needs only to know it is one.

    Reusing the order's own body here would demand a city and a street before
    answering — withholding the figure at exactly the moment the customer is
    deciding whether delivery is worth it.
    """
    response = await client.post("/orders/reprice", json=a_reprice("courier"))

    assert response.status_code == 200, response.text


async def test_repricing_tracks_the_delivery_choice(client: AsyncClient) -> None:
    collected = (await client.post("/orders/reprice", json=a_reprice("pickup"))).json()
    shipped = (await client.post("/orders/reprice", json=a_reprice("courier"))).json()

    collected_codes = [line["code"] for line in collected["breakdown"]["lines"]]
    shipped_codes = [line["code"] for line in shipped["breakdown"]["lines"]]

    assert "logistics.shipping" not in collected_codes
    assert "logistics.shipping" in shipped_codes
    assert Decimal(shipped["breakdown"]["total"]) > Decimal(collected["breakdown"]["total"])


async def test_the_repriced_figure_is_the_one_charged(client: AsyncClient) -> None:
    """The one thing a re-price must never get wrong.

    Both paths run the same spec builder against the same engine, so a checkout
    that shows one number and an order that charges another would mean two ways
    of pricing had grown — which is what ADR-0002 exists to prevent.
    """
    auth = await token_for(client, "buyer@example.com")
    payload = an_order_payload()
    payload["delivery"] = {"method": "courier", "city": "Москва", "address": "Тверская 1"}

    shown = (await client.post("/orders/reprice", json=a_reprice("courier"))).json()
    charged = (await client.post("/orders", json=payload, headers=auth)).json()

    assert Decimal(shown["breakdown"]["total"]) == Decimal(charged["total"])


async def test_repricing_writes_nothing(client: AsyncClient) -> None:
    """It answers a question. A visitor weighing delivery has not ordered anything."""
    auth = await token_for(client, "buyer@example.com")
    await client.post("/orders/reprice", json=a_reprice("courier"))

    assert (await client.get("/orders/mine", headers=auth)).json()["rows"] == []


async def test_anyone_may_ask_what_delivery_costs(client: AsyncClient) -> None:
    """No session. Refusing would only mean showing a signed-out visitor a stale
    number on a screen whose whole claim is that its figures are current."""
    assert (await client.post("/orders/reprice", json=a_reprice("pickup"))).status_code == 200
