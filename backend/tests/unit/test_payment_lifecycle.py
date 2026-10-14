"""A payment's life at the gateway, beyond the happy path.

The security review's second pass (#27, `docs/SECURITY-REVIEW.md` §2b) recorded
five gaps in the payment lifecycle that were mechanical rather than decisions,
and this file is the proof each is closed:

* **a payment the gateway cancelled is recorded**, by notification or by asking,
  so the customer can start a fresh one instead of being handed the dead page;
* **a settlement whose notification was lost is recovered** the next time the
  customer opens checkout;
* **two concurrent starts make one payment**, and the loser never reaches the
  gateway;
* **a settlement in the wrong currency is refused**, as a wrong amount is;
* **every notification is linked to its payment**, so reconciliation can join them.

The fifth gap the review recorded — a settlement arriving for an order the farm
already cancelled — is a policy decision and is not here (§5(d)).

The world is rebuilt here rather than imported, for the reason
`test_payment_provider_binding.py` gives.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import DraftLine, OrderingService, OrderStatus, PlaceOrder
from printorian.contexts.payments import (
    MockPaymentProvider,
    PaymentsService,
    PaymentStatus,
    StartPayment,
)
from printorian.contexts.payments.models import Payment, PaymentNotification
from printorian.contexts.payments.providers.mock import SIGNATURE_HEADER, VALID_SIGNATURE
from printorian.contexts.payments.schemas import PaymentView
from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
)
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import DomainRuleViolationError
from printorian.core.events import EventBus
from printorian.core.units import Duration, Mass

RATES = RateSnapshot()


@pytest.fixture
def ordering(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> OrderingService:
    return OrderingService(db_session, clock, bus)


@pytest.fixture
def payments(
    db_session: AsyncSession, clock: FixedClock, bus: EventBus, ordering: OrderingService
) -> PaymentsService:
    return PaymentsService(db_session, clock, bus, ordering)


@pytest.fixture
def gateway(settings: Settings) -> MockPaymentProvider:
    return MockPaymentProvider(settings=settings)


async def an_order(ordering: OrderingService):  # type: ignore[no-untyped-def]
    return await ordering.place(
        PlaceOrder(
            customer_email="buyer@example.com",
            lines=[
                DraftLine(
                    model_name="part.stl",
                    material_code="pla-black",
                    estimated_minutes=Decimal(180),
                    estimated_grams=Decimal(90),
                )
            ],
        ),
        price(
            PriceSpec(
                estimate=PrintEstimate(print_time=Duration.from_hours(3), material_mass=Mass(90)),
                material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
            ),
            RATES,
        ),
        RATES,
    )


def upstream_id(payment: PaymentView) -> str:
    assert payment.confirmation_url is not None
    return f"mock-{payment.confirmation_url.rsplit('/', 1)[-1]}"


def notification(provider_id: str, status: str, amount: Decimal, event_id: str) -> bytes:
    return json.dumps(
        {
            "payment_id": provider_id,
            "status": status,
            "amount": str(amount),
            "event_id": event_id,
        }
    ).encode()


HEADERS = {SIGNATURE_HEADER: VALID_SIGNATURE}


# ------------------------------------------------------------ cancelled upstream


async def test_a_cancellation_notification_is_recorded_and_checkout_starts_afresh(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """Nothing used to set `cancelled`; `start` handed back the dead page for ever."""
    order = await an_order(ordering)
    first = await payments.start(StartPayment(order_id=order.id), gateway)
    gateway.gateway_cancels(upstream_id(first))

    seen = await payments.handle_webhook(
        gateway, HEADERS, notification(upstream_id(first), "cancelled", order.total, "evt-c")
    )

    assert seen is not None
    assert seen.status is PaymentStatus.CANCELLED
    # The order is not cancelled with it: the customer may simply pay again.
    assert (await ordering.get(order.id)).status is OrderStatus.AWAITING_PAYMENT

    second = await payments.start(StartPayment(order_id=order.id), gateway)
    assert second.id != first.id
    assert second.status is PaymentStatus.PENDING


async def test_a_cancellation_whose_notification_was_lost_is_found_by_asking(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    first = await payments.start(StartPayment(order_id=order.id), gateway)
    gateway.gateway_cancels(upstream_id(first))  # and no notification ever arrives

    second = await payments.start(StartPayment(order_id=order.id), gateway)

    assert second.id != first.id
    assert (await payments.get(first.id)).status is PaymentStatus.CANCELLED


async def test_a_settlement_whose_notification_was_lost_is_settled_at_checkout(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """The customer paid, the notification never came, and they reload checkout."""
    order = await an_order(ordering)
    first = await payments.start(StartPayment(order_id=order.id), gateway)
    gateway.customer_pays(upstream_id(first))

    again = await payments.start(StartPayment(order_id=order.id), gateway)

    assert again.id == first.id
    assert again.status is PaymentStatus.SUCCEEDED
    assert (await ordering.get(order.id)).status is OrderStatus.PAID


# ------------------------------------------------------------ concurrent starts


async def test_the_database_refuses_a_second_live_payment_for_one_order(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    order = await an_order(ordering)
    await payments.start(StartPayment(order_id=order.id), gateway)

    db_session.add(
        Payment(
            order_id=order.id,
            provider="mock",
            idempotency_key="second-live",
            status=PaymentStatus.PENDING,
            amount=order.total,
            currency=order.currency,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_the_loser_of_a_double_click_gets_the_winner_and_never_reaches_the_gateway(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both requests passed the check before either inserted. Forced here by having
    the second request's check see nothing, which is exactly what it saw in the race."""
    order = await an_order(ordering)
    winner = await payments.start(StartPayment(order_id=order.id), gateway)

    real = payments._live_payment
    calls = 0

    async def blind_once(order_id):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real(order_id)

    monkeypatch.setattr(payments, "_live_payment", blind_once)
    gateway.fail_create = True  # reaching the gateway would raise

    loser = await payments.start(StartPayment(order_id=order.id), gateway)

    assert loser.id == winner.id


# ------------------------------------------------------------ currency and linkage


async def test_a_settlement_in_another_currency_is_refused(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """`1 500.00` is the same number in roubles and in tenge."""
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    gateway.customer_pays(upstream_id(payment), currency="KZT")

    with pytest.raises(DomainRuleViolationError) as raised:
        await payments.handle_webhook(
            gateway,
            HEADERS,
            notification(upstream_id(payment), "succeeded", order.total, "evt-k"),
        )
    assert raised.value.code == "error.payments.currency_mismatch"
    assert (await payments.get(payment.id)).status is PaymentStatus.PENDING


async def test_every_notification_is_linked_to_the_payment_it_was_about(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    gateway.customer_pays(upstream_id(payment))

    await payments.handle_webhook(
        gateway, HEADERS, notification(upstream_id(payment), "succeeded", order.total, "evt-s")
    )

    rows = list(await db_session.scalars(select(PaymentNotification)))
    assert [row.payment_id for row in rows] == [payment.id]
