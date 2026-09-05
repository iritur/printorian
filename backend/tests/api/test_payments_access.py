"""Who may see, start and reverse a payment.

Separate from `test_checkout_api.py`, which asks whether checkout *works*. This
file asks who it works for, and the two questions grew one file past the length
the project holds itself to.

Two rules are pinned here and they are deliberately different from each other:

* A **customer** reaches a payment through the order it belongs to, and only their
  own. Payments know nothing about who placed an order, so the scoping happens in
  the delivery layer where the order is available.
* **Staff** reach it through `VIEW_FINANCIALS` and not through `VIEW_ALL_ORDERS`.
  A `PaymentView` is rubles end to end — amount, currency, refunded_amount — and
  CLAUDE.md §1 keeps the money permission separate from the production ones for
  exactly that reason.
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


async def a_started_payment(client: AsyncClient, auth: dict[str, str]) -> tuple[dict, dict]:
    """An order with a live mock payment against it, for the reading cases."""
    order = await place(client, auth)
    started = await client.post(
        "/payments", json={"order_id": order["id"], "provider": "mock"}, headers=auth
    )
    assert started.status_code == 201, started.text
    return order, started.json()


async def a_settled_payment(client: AsyncClient, auth: dict[str, str]) -> tuple[dict, dict, str]:
    """The same, taken all the way through the gateway so it can be refunded.

    Goes through the real webhook rather than advancing the order by hand: the
    refund cases are about the money path, and a payment settled around the
    settlement routine is not the payment those cases mean to talk about.
    """
    order, payment = await a_started_payment(client, auth)
    provider_id = f"mock-{payment['confirmation_url'].rsplit('/', 1)[-1]}"
    client._transport.app.state.payment_gateways["mock"].customer_pays(provider_id)

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
    assert notified.status_code == 200, notified.text
    return order, payment, provider_id


# --------------------------------------------------------- one customer


async def test_a_customer_cannot_list_another_customers_payments(client: AsyncClient) -> None:
    """The amounts a stranger paid are nobody else's business.

    The refusal is deliberately identical to a plain permission failure, the same
    as the order route it borrows the rule from.
    """
    buyer_auth = await token_for(client, "buyer@example.com")
    order, _ = await a_started_payment(client, buyer_auth)

    rival_auth = await token_for(client, "rival@example.com")
    response = await client.get(f"/payments/order/{order['id']}", headers=rival_auth)

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "error.permission_denied"


async def test_a_customer_cannot_read_another_customers_payment(client: AsyncClient) -> None:
    """Reached by payment id rather than order id, which is a second door."""
    buyer_auth = await token_for(client, "buyer@example.com")
    _, payment = await a_started_payment(client, buyer_auth)

    rival_auth = await token_for(client, "rival@example.com")
    response = await client.get(f"/payments/{payment['id']}", headers=rival_auth)

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "error.permission_denied"


async def test_a_customer_cannot_start_a_payment_on_another_customers_order(
    client: AsyncClient,
) -> None:
    """And the order must be exactly where it was.

    A 403 that has already moved the order to `awaiting_payment` is not a fix: it
    refuses the response while keeping the side effect, and the side effect is the
    part a stranger wanted.
    """
    buyer_auth = await token_for(client, "buyer@example.com")
    order = await place(client, buyer_auth)

    rival_auth = await token_for(client, "rival@example.com")
    response = await client.post(
        "/payments", json={"order_id": order["id"], "provider": "mock"}, headers=rival_auth
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "error.permission_denied"
    still = await client.get(f"/orders/{order['id']}", headers=buyer_auth)
    assert still.json()["status"] == "draft"


async def test_a_customer_reads_their_own_payments(client: AsyncClient) -> None:
    """The guard must not pass by refusing everybody."""
    buyer_auth = await token_for(client, "buyer@example.com")
    order, payment = await a_started_payment(client, buyer_auth)

    listed = await client.get(f"/payments/order/{order['id']}", headers=buyer_auth)
    one = await client.get(f"/payments/{payment['id']}", headers=buyer_auth)

    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [payment["id"]]
    assert one.status_code == 200, one.text
    assert one.json()["order_id"] == order["id"]


# -------------------------------------------------------------- staff


async def test_staff_reading_payments_need_view_financials(client: AsyncClient) -> None:
    """Money is gated on the money permission, not on seeing every order.

    This changes no screen today: `policies.py` gives MANAGER both
    `VIEW_ALL_ORDERS` and `VIEW_FINANCIALS`, and the owner holds all of them. The
    test exists so that the day those two are split apart, a test fails rather
    than a customer's amounts appearing on a production screen.
    """
    buyer_auth = await token_for(client, "buyer@example.com")
    order, payment = await a_started_payment(client, buyer_auth)

    boss_auth = await token_for(client, "boss@example.com")
    assert (
        await client.get(f"/payments/order/{order['id']}", headers=boss_auth)
    ).status_code == 200
    assert (await client.get(f"/payments/{payment['id']}", headers=boss_auth)).status_code == 200

    engineer_auth = await token_for(client, "engineer@example.com")
    refused = await client.get(f"/payments/order/{order['id']}", headers=engineer_auth)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "error.permission_denied"
    assert (
        await client.get(f"/payments/{payment['id']}", headers=engineer_auth)
    ).status_code == 403


async def test_an_unknown_order_id_is_a_404_not_an_empty_list(client: AsyncClient) -> None:
    """An empty grid reads as "this order took no payments", which is a claim.

    ADR-0007 / CLAUDE.md §1: the farm never measured anything about an order it
    does not have, so the honest answer is that there is no such order.
    """
    boss_auth = await token_for(client, "boss@example.com")

    absent = new_id()
    response = await client.get(f"/payments/order/{absent}", headers=boss_auth)

    assert response.status_code == 404, response.text
    assert response.json() != []


# ------------------------------------------------------------ refunding


async def test_the_refund_route_ignores_a_caller_supplied_gateway(client: AsyncClient) -> None:
    """The gateway a refund goes through is the one that took the money.

    Before this was fixed the route took `provider_name` from the query string, so
    `?provider_name=manual` sent a card refund into `ManualPaymentProvider`, whose
    contract is "a person will send the money" and which therefore always reports
    success. The payment read REFUNDED while the gateway holding the money had
    never been told — the database and the money disagreeing, with the database
    the copy. So the assertion is on the gateway's own ledger, not on the view.
    """
    buyer_auth = await token_for(client, "buyer@example.com")
    order, payment, provider_id = await a_settled_payment(client, buyer_auth)
    gateway = client._transport.app.state.payment_gateways["mock"]

    boss_auth = await token_for(client, "boss@example.com")
    response = await client.post(
        f"/payments/{payment['id']}/refund?provider_name=manual",
        json={"reason": "refund.requested"},
        headers=boss_auth,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refunded"
    assert gateway.refunded(provider_id) == Decimal(order["total"])
