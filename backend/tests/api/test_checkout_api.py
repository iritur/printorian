"""Checkout over HTTP: place, pay, track.

The authorization cases matter most here. A customer must be able to see their own
order and must not be able to see anyone else's, and neither of those can be left
to the client behaving well.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import AsyncClient

from printorian.contexts.payments.providers.mock import SIGNATURE_HEADER, VALID_SIGNATURE
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


# ------------------------------------------------------------- placing


async def test_placing_prices_the_order_server_side(client: AsyncClient) -> None:
    """The request carries no price, and the response carries a pinned breakdown."""
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)

    assert order["status"] == "draft"
    assert Decimal(order["total"]) > 0
    assert order["rate_snapshot_id"].startswith("rates_")
    assert order["price_breakdown"]["lines"]
    assert order["number"].startswith("PR-")


async def test_placing_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/orders", json=an_order_payload())).status_code == 401


async def test_an_unknown_material_is_refused(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    payload = an_order_payload()
    payload["lines"][0]["material_code"] = "unobtainium"  # type: ignore[index]

    response = await client.post("/orders", json=payload, headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "error.inventory.spec_not_found"


# ------------------------------------------------------- authorization


async def test_a_customer_sees_their_own_order(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)

    response = await client.get(f"/orders/{order['id']}", headers=auth)
    assert response.status_code == 200


async def test_a_customer_cannot_see_someone_elses_order(client: AsyncClient) -> None:
    """And the refusal must not reveal that the order exists."""
    owner_auth = await token_for(client, "buyer@example.com")
    order = await place(client, owner_auth)

    rival_auth = await token_for(client, "rival@example.com")
    response = await client.get(f"/orders/{order['id']}", headers=rival_auth)

    assert response.status_code == 403
    assert response.json()["code"] == "error.permission_denied"


async def test_staff_may_see_any_order(client: AsyncClient) -> None:
    buyer_auth = await token_for(client, "buyer@example.com")
    order = await place(client, buyer_auth)

    boss_auth = await token_for(client, "boss@example.com")
    assert (await client.get(f"/orders/{order['id']}", headers=boss_auth)).status_code == 200


async def test_the_cabinet_shows_only_the_callers_orders(client: AsyncClient) -> None:
    buyer_auth = await token_for(client, "buyer@example.com")
    await place(client, buyer_auth)

    rival_auth = await token_for(client, "rival@example.com")
    mine = await client.get("/orders/mine", headers=rival_auth)

    assert mine.status_code == 200
    assert mine.json()["total"] == 0


async def test_the_admin_table_is_closed_to_customers(client: AsyncClient) -> None:
    buyer_auth = await token_for(client, "buyer@example.com")
    await place(client, buyer_auth)

    assert (await client.get("/orders", headers=buyer_auth)).status_code == 403

    boss_auth = await token_for(client, "boss@example.com")
    table = await client.get("/orders", headers=boss_auth)
    assert table.status_code == 200
    assert table.json()["total"] == 1
    assert {row["status"] for row in table.json()["counts"]} >= {"draft", "paid"}


async def test_only_managers_may_advance_an_order(client: AsyncClient) -> None:
    buyer_auth = await token_for(client, "buyer@example.com")
    order = await place(client, buyer_auth)

    refused = await client.post(
        f"/orders/{order['id']}/advance",
        json={"target": "paid", "reason": "nice try"},
        headers=buyer_auth,
    )
    assert refused.status_code == 403


async def test_an_illegal_transition_is_refused_over_http(client: AsyncClient) -> None:
    buyer_auth = await token_for(client, "buyer@example.com")
    order = await place(client, buyer_auth)
    boss_auth = await token_for(client, "boss@example.com")

    response = await client.post(
        f"/orders/{order['id']}/advance",
        json={"target": "shipped"},
        headers=boss_auth,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "error.ordering.invalid_transition"


# ------------------------------------------------------------- paying


async def test_the_full_checkout_flow(client: AsyncClient) -> None:
    """Place, start payment, customer pays, gateway notifies, order becomes paid.

    Goes through the real webhook endpoint rather than around it — the gateway
    instance is cached on the app, so it remembers the payment between requests
    exactly as an external system would.
    """
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)

    started = await client.post(
        "/payments", json={"order_id": order["id"], "provider": "mock"}, headers=auth
    )
    assert started.status_code == 201, started.text
    payment = started.json()
    assert Decimal(payment["amount"]) == Decimal(order["total"])
    assert payment["confirmation_url"]

    assert (await client.get(f"/orders/{order['id']}", headers=auth)).json()[
        "status"
    ] == "awaiting_payment"

    # The customer pays at the gateway.
    provider_id = f"mock-{payment['confirmation_url'].rsplit('/', 1)[-1]}"
    gateway = client._transport.app.state.payment_gateways["mock"]
    gateway.customer_pays(provider_id)

    # The gateway then notifies us, and only then is the order paid.
    notified = await client.post(
        "/payments/webhook/mock",
        headers={SIGNATURE_HEADER: VALID_SIGNATURE},
        content=json.dumps(
            {
                "payment_id": provider_id,
                "status": "succeeded",
                "amount": order["total"],
                "event_id": "evt-checkout",
            }
        ),
    )
    assert notified.status_code == 200
    assert notified.json()["status"] == "processed"

    paid = await client.get(f"/orders/{order['id']}", headers=auth)
    assert paid.json()["status"] == "paid"
    assert paid.json()["paid_at"] is not None

    assert (await client.get(f"/payments/{payment['id']}", headers=auth)).json()[
        "status"
    ] == "succeeded"


async def test_a_redelivered_webhook_is_reported_as_a_duplicate(client: AsyncClient) -> None:
    """A repeat must answer 200, not an error: a non-2xx just makes it retry again."""
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)
    started = await client.post(
        "/payments", json={"order_id": order["id"], "provider": "mock"}, headers=auth
    )
    payment = started.json()
    provider_id = f"mock-{payment['confirmation_url'].rsplit('/', 1)[-1]}"
    client._transport.app.state.payment_gateways["mock"].customer_pays(provider_id)

    body = json.dumps(
        {
            "payment_id": provider_id,
            "status": "succeeded",
            "amount": order["total"],
            "event_id": "evt-once",
        }
    )
    headers = {SIGNATURE_HEADER: VALID_SIGNATURE}

    first = await client.post("/payments/webhook/mock", headers=headers, content=body)
    second = await client.post("/payments/webhook/mock", headers=headers, content=body)

    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"


async def test_a_forged_webhook_is_rejected(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)
    started = await client.post(
        "/payments", json={"order_id": order["id"], "provider": "mock"}, headers=auth
    )
    payment = started.json()
    provider_id = f"mock-{payment['confirmation_url'].rsplit('/', 1)[-1]}"

    response = await client.post(
        "/payments/webhook/mock",
        headers={SIGNATURE_HEADER: "forged"},
        content=json.dumps(
            {"payment_id": provider_id, "status": "succeeded", "amount": order["total"]}
        ),
    )

    assert response.status_code == 502
    assert response.json()["code"] == "error.payments.webhook_unverified"
    assert (await client.get(f"/orders/{order['id']}", headers=auth)).json()[
        "status"
    ] == "awaiting_payment"


async def test_the_webhook_endpoint_needs_no_session(client: AsyncClient) -> None:
    """Gateways cannot sign in. Safety comes from verification, not from auth."""
    response = await client.post(
        "/payments/webhook/mock",
        headers={SIGNATURE_HEADER: VALID_SIGNATURE},
        content=json.dumps({"payment_id": "mock-nope", "status": "succeeded", "amount": "1.00"}),
    )
    # Verified, but for a payment we do not have — a 404, never a 401.
    assert response.status_code == 404


async def test_only_owners_may_refund(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)
    started = await client.post(
        "/payments", json={"order_id": order["id"], "provider": "mock"}, headers=auth
    )
    payment_id = started.json()["id"]

    refused = await client.post(
        f"/payments/{payment_id}/refund", json={"reason": "refund.requested"}, headers=auth
    )
    assert refused.status_code == 403
