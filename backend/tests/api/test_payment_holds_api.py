"""Held payments over HTTP: who may see them and who may decide (security review §5(d)).

* **Seeing** the list is `VIEW_FINANCIALS` — it is rubles — so an engineer and a
  customer are refused, and a manager is not.
* **Releasing** is `ISSUE_REFUND`, the narrower money permission (§5(b), kept as
  intended): deciding not to send money back is the same decision as sending it.
  So the manager who can see the list cannot clear it.
* A release without a reason is refused; the reason is the record.
* `/payments/held` is declared ahead of `/payments/{payment_id}`, and a test
  that reaches it at all proves the order — the other way round it is a 422.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.contexts.payments.providers.mock import SIGNATURE_HEADER, VALID_SIGNATURE
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import PASSWORD, a_shop, place, token_for


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


async def a_late_settlement(client: AsyncClient) -> tuple[dict, dict]:
    """A customer pays on a page the farm gave up on, after the order was cancelled."""
    buyer = await token_for(client, "buyer@example.com")
    boss = await token_for(client, "boss@example.com")
    order = await place(client, buyer)
    started = await client.post(
        "/payments", json={"order_id": order["id"], "provider": "mock"}, headers=buyer
    )
    assert started.status_code == 201, started.text
    payment = started.json()

    cancelled = await client.post(
        f"/orders/{order['id']}/advance", json={"target": "cancelled"}, headers=boss
    )
    assert cancelled.status_code == 200, cancelled.text

    provider_id = f"mock-{payment['confirmation_url'].rsplit('/', 1)[-1]}"
    client._transport.app.state.payment_gateways["mock"].customer_pays(provider_id)  # type: ignore[attr-defined]
    notified = await client.post(
        "/payments/webhook/mock",
        headers={SIGNATURE_HEADER: VALID_SIGNATURE},
        content=json.dumps(
            {
                "payment_id": provider_id,
                "status": "succeeded",
                "amount": order["total"],
                "event_id": f"evt-{provider_id}",
            }
        ),
    )
    # Accepted: refusing the notification would not give the money back.
    assert notified.status_code == 200, notified.text
    return order, payment


async def a_manager(client: AsyncClient) -> dict[str, str]:
    boss = await token_for(client, "boss@example.com")
    created = await client.post(
        "/users",
        json={
            "email": "manager@example.com",
            "display_name": "Manager",
            "password": PASSWORD,
            "role": "manager",
        },
        headers=boss,
    )
    assert created.status_code == 201, created.text
    return await token_for(client, "manager@example.com")


async def test_the_owner_sees_money_that_arrived_for_a_cancelled_order(
    client: AsyncClient,
) -> None:
    order, payment = await a_late_settlement(client)
    boss = await token_for(client, "boss@example.com")

    listed = await client.get("/payments/held", headers=boss)

    assert listed.status_code == 200, listed.text
    [held] = listed.json()
    assert held["payment_id"] == payment["id"]
    assert held["order_id"] == order["id"]
    assert held["order_status"] == "cancelled"
    assert held["status"] == "succeeded"


async def test_seeing_held_money_needs_view_financials(client: AsyncClient) -> None:
    await a_late_settlement(client)

    for email in ("engineer@example.com", "buyer@example.com"):
        refused = await client.get("/payments/held", headers=await token_for(client, email))
        assert refused.status_code == 403, (email, refused.text)

    manager = await a_manager(client)
    assert (await client.get("/payments/held", headers=manager)).status_code == 200


async def test_releasing_held_money_needs_issue_refund_and_a_reason(client: AsyncClient) -> None:
    _, payment = await a_late_settlement(client)
    url = f"/payments/held/{payment['id']}/release"

    manager = await a_manager(client)
    refused = await client.post(url, json={"note": "keep it"}, headers=manager)
    assert refused.status_code == 403, refused.text

    boss = await token_for(client, "boss@example.com")
    assert (await client.post(url, json={"note": ""}, headers=boss)).status_code == 422

    released = await client.post(url, json={"note": "Re-ordered by phone"}, headers=boss)
    assert released.status_code == 200, released.text
    assert (await client.get("/payments/held", headers=boss)).json() == []
