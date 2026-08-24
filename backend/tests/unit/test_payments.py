"""Payments: idempotency, reconciliation, refunds.

Payment integrations fail the same way everywhere — duplicate webhooks, forged
notifications, client-supplied amounts, double refunds. Each of those is a test here
rather than an incident later.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import (
    DraftLine,
    OrderingService,
    OrderStatus,
    PlaceOrder,
)
from printorian.contexts.payments import (
    MockPaymentProvider,
    PaymentsService,
    PaymentStatus,
    StartPayment,
    WebhookVerificationError,
)
from printorian.contexts.payments.providers.mock import SIGNATURE_HEADER, VALID_SIGNATURE
from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
)
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import ConflictError, DomainRuleViolationError
from printorian.core.events import EventBus
from printorian.core.units import Duration, Mass

RATES = RateSnapshot()


def a_breakdown():
    return price(
        PriceSpec(
            estimate=PrintEstimate(print_time=Duration.from_hours(3), material_mass=Mass(90)),
            material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
        ),
        RATES,
    )


@pytest.fixture
def ordering(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> OrderingService:
    return OrderingService(db_session, clock, bus)


@pytest.fixture
def gateway(settings: Settings) -> MockPaymentProvider:
    return MockPaymentProvider(settings=settings)


@pytest.fixture
def payments(
    db_session: AsyncSession, clock: FixedClock, bus: EventBus, ordering: OrderingService
) -> PaymentsService:
    return PaymentsService(db_session, clock, bus, ordering)


async def an_order(ordering: OrderingService):
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
        a_breakdown(),
        RATES,
    )


def webhook(payment_id: str, amount: Decimal, *, event_id: str = "evt-1") -> tuple[dict, bytes]:
    body = json.dumps(
        {
            "payment_id": payment_id,
            "status": "succeeded",
            "amount": str(amount),
            "event_id": event_id,
        }
    ).encode()
    return {SIGNATURE_HEADER: VALID_SIGNATURE}, body


# ------------------------------------------------------------- starting


async def test_starting_takes_the_amount_from_the_order(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """The client never states a price. It has no field in which to try."""
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)

    assert payment.amount == order.total
    assert payment.status is PaymentStatus.PENDING
    assert payment.confirmation_url


async def test_starting_moves_the_order_to_awaiting_payment(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    await payments.start(StartPayment(order_id=order.id), gateway)

    assert (await ordering.get(order.id)).status is OrderStatus.AWAITING_PAYMENT


async def test_starting_twice_reuses_the_live_payment(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """A customer who reloads checkout must not create a second charge."""
    order = await an_order(ordering)
    first = await payments.start(StartPayment(order_id=order.id), gateway)
    second = await payments.start(StartPayment(order_id=order.id), gateway)

    assert first.id == second.id


async def test_cannot_start_a_payment_for_an_already_paid_order(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    await pay(payments, gateway, payment, order.total)

    with pytest.raises(ConflictError):
        await payments.start(StartPayment(order_id=order.id), gateway)


async def test_the_chosen_payment_method_reaches_the_gateway(
    payments: PaymentsService, ordering: OrderingService
) -> None:
    """T-Pay is a customer's choice, not a deployment secret, so it must travel.

    The schema field is ``payment_method``; the provider contract field is
    ``payment_method_type``. That mapping is exactly the kind of one-line seam that
    silently drops a value, so it is pinned rather than left to naming luck.
    """
    from printorian.contexts.payments.provider import PaymentRequest, ProviderPayment

    seen: list[PaymentRequest] = []

    class RecordingProvider:
        name = "recording"

        async def create(self, request: PaymentRequest) -> ProviderPayment:
            seen.append(request)
            return ProviderPayment(
                provider_payment_id="rp-1",
                status=PaymentStatus.PENDING,
                amount=request.amount,
                currency=request.currency,
                confirmation_url="https://gateway.example/pay",
            )

    order = await an_order(ordering)
    await payments.start(
        StartPayment(order_id=order.id, payment_method="tinkoff_bank"),
        RecordingProvider(),
    )

    assert seen[0].payment_method_type == "tinkoff_bank"


def provider_id_of(payment) -> str:
    return f"mock-{payment.confirmation_url.rsplit('/', 1)[-1]}"


async def pay(payments: PaymentsService, gateway: MockPaymentProvider, payment, amount: Decimal):
    """The full real-world sequence: customer pays, then the gateway notifies us."""
    provider_id = provider_id_of(payment)
    gateway.customer_pays(provider_id)
    return await payments.handle_webhook(gateway, *webhook(provider_id, amount))


# ------------------------------------------------------------- settling


async def test_a_verified_webhook_settles_and_pays_the_order(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)

    settled = await pay(payments, gateway, payment, order.total)

    assert settled is not None
    assert settled.status is PaymentStatus.SUCCEEDED
    assert settled.settled_at is not None
    # Order and money move together, in one transaction.
    assert (await ordering.get(order.id)).status is OrderStatus.PAID


async def test_a_repeated_webhook_changes_nothing(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """Providers retry by design. A second delivery must be a no-op, not a bug."""
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    provider_id = provider_id_of(payment)
    gateway.customer_pays(provider_id)
    headers, body = webhook(provider_id, order.total)

    first = await payments.handle_webhook(gateway, headers, body)
    second = await payments.handle_webhook(gateway, headers, body)

    assert first is not None
    assert second is None  # recognised as a duplicate
    assert (await payments.get(payment.id)).status is PaymentStatus.SUCCEEDED


async def test_an_unsigned_webhook_is_rejected(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """An unauthenticated notification that marks orders paid is free prints."""
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    gateway.customer_pays(provider_id_of(payment))
    _, body = webhook(provider_id_of(payment), order.total)

    with pytest.raises(WebhookVerificationError):
        await payments.handle_webhook(gateway, {"x-mock-signature": "forged"}, body)

    assert (await ordering.get(order.id)).status is OrderStatus.AWAITING_PAYMENT


async def test_settlement_is_confirmed_with_the_gateway_not_the_body(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    """A body claiming a different amount cannot inflate what was captured.

    The service re-reads the payment from the provider, so the gateway's own figure
    decides — and it matches the order, so settlement succeeds regardless of the lie.
    """
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    provider_id = provider_id_of(payment)
    gateway.customer_pays(provider_id)
    headers, body = webhook(provider_id, Decimal("1.00"))  # body understates wildly

    settled = await payments.handle_webhook(gateway, headers, body)

    assert settled is not None
    assert settled.status is PaymentStatus.SUCCEEDED
    assert settled.amount == order.total


async def test_a_mismatched_capture_is_refused(payments: PaymentsService) -> None:
    from printorian.contexts.payments.policies import reconcile

    reconcile(Decimal("100.00"), Decimal("100.005"))  # inside tolerance
    with pytest.raises(DomainRuleViolationError) as excinfo:
        reconcile(Decimal("100.00"), Decimal("95.00"))
    assert excinfo.value.code == "error.payments.amount_mismatch"


async def test_a_webhook_for_an_unknown_payment_is_not_silently_ignored(
    payments: PaymentsService, gateway: MockPaymentProvider
) -> None:
    from printorian.core.errors import NotFoundError

    headers, body = webhook("mock-nonexistent", Decimal(10))
    with pytest.raises((NotFoundError, Exception)):
        await payments.handle_webhook(gateway, headers, body)


# ------------------------------------------------------------ refunding


async def test_full_refund_marks_the_payment_refunded(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    await pay(payments, gateway, payment, order.total)

    refunded = await payments.refund(payment.id, gateway)

    assert refunded.status is PaymentStatus.REFUNDED
    assert refunded.refunded_amount == order.total
    assert refunded.refundable == 0


async def test_partial_refund_leaves_the_payment_partially_refunded(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    await pay(payments, gateway, payment, order.total)

    refunded = await payments.refund(payment.id, gateway, amount=Decimal("100.00"))

    assert refunded.status is PaymentStatus.PARTIALLY_REFUNDED
    assert refunded.refunded_amount == Decimal("100.00")


async def test_refunds_can_never_exceed_what_is_held(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    await pay(payments, gateway, payment, order.total)
    await payments.refund(payment.id, gateway, amount=Decimal("100.00"))

    with pytest.raises(DomainRuleViolationError) as excinfo:
        await payments.refund(payment.id, gateway, amount=order.total)
    assert excinfo.value.code == "error.payments.refund_exceeds_balance"


async def test_an_unsettled_payment_cannot_be_refunded(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)

    with pytest.raises(ConflictError):
        await payments.refund(payment.id, gateway)


async def test_late_delivery_credit_is_returned_as_a_partial_refund(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    clock: FixedClock,
) -> None:
    """The scenario's rule: keep the customer waiting and the model gets cheaper.

    For an already-charged customer that has to be actual money going back.
    """
    from datetime import timedelta

    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    await pay(payments, gateway, payment, order.total)

    clock.advance(timedelta(days=8))
    order = await ordering.refresh_sla_credit(order.id)
    assert order.sla_credit > 0

    refunded = await payments.refund_sla_credit(payment.id, gateway)

    assert refunded.refunded_amount == order.sla_credit
    assert refunded.status is PaymentStatus.PARTIALLY_REFUNDED
    assert refunded.refunds[-1].reason == "refund.sla_credit"


async def test_settling_the_sla_credit_twice_returns_nothing_extra(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    clock: FixedClock,
) -> None:
    from datetime import timedelta

    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    await pay(payments, gateway, payment, order.total)

    clock.advance(timedelta(days=8))
    await ordering.refresh_sla_credit(order.id)

    once = await payments.refund_sla_credit(payment.id, gateway)
    twice = await payments.refund_sla_credit(payment.id, gateway)
    assert once.refunded_amount == twice.refunded_amount
