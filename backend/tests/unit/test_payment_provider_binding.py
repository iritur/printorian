"""A refund goes back through the gateway that took the money, or not at all.

Its own file rather than more of `test_payments.py`, which is already close to the
length the project holds itself to and is about what a refund *does*. This is about
which gateway one is allowed to go through, which is a different question and the
one that had no answer.

The world is rebuilt here rather than imported from `test_payments.py`: importing
one test module into another makes the fixtures of both hostage to the other's
edits, and this one needs only an order and a settled payment.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import DraftLine, OrderingService, PlaceOrder
from printorian.contexts.payments import (
    ManualPaymentProvider,
    MockPaymentProvider,
    PaymentsService,
    PaymentStatus,
    StartPayment,
)
from printorian.contexts.payments.models import Refund
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
from printorian.core.errors import ConflictError
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


async def a_settled_payment(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
):
    """An order paid in full through the mock gateway.

    Settled through the webhook rather than by hand, because the guard under test
    sits on the money path and a payment that reached SUCCEEDED by another route is
    not the payment an operator would be refunding.
    """
    order = await ordering.place(
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
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    provider_id = f"mock-{payment.confirmation_url.rsplit('/', 1)[-1]}"
    gateway.customer_pays(provider_id)

    body = json.dumps(
        {
            "payment_id": provider_id,
            "status": "succeeded",
            "amount": str(order.total),
            "event_id": f"evt-{provider_id}",
        }
    ).encode()
    settled = await payments.handle_webhook(gateway, {SIGNATURE_HEADER: VALID_SIGNATURE}, body)
    assert settled is not None and settled.status is PaymentStatus.SUCCEEDED
    return order, settled


async def refund_rows(db_session: AsyncSession) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(Refund)) or 0)


async def test_a_refund_through_a_different_gateway_is_refused(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    """The mock took the money; the manual provider must not be allowed to return it.

    `ManualPaymentProvider.refund` always reports success — correctly, because its
    contract is that a person will send the money afterwards. So without this guard
    the refusal is invisible: the payment reads REFUNDED, a refund note is issued
    to the customer, and the gateway actually holding the money was never told.
    That is why the assertions are about what was *not* written, and not merely
    about the exception.
    """
    _, settled = await a_settled_payment(payments, ordering, gateway)

    with pytest.raises(ConflictError) as excinfo:
        await payments.refund(settled.id, ManualPaymentProvider())

    assert excinfo.value.code == "error.payments.provider_mismatch"
    # ADR-0012: the pair a client needs in order to say anything useful, as
    # structured details rather than a sentence.
    assert excinfo.value.details["expected"] == "mock"
    assert excinfo.value.details["actual"] == "manual"

    assert await refund_rows(db_session) == 0
    after = await payments.get(settled.id)
    assert after.status is PaymentStatus.SUCCEEDED
    assert after.refunded_amount == Decimal(0)


async def test_an_sla_credit_refund_uses_the_gateway_that_took_the_money(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    clock: FixedClock,
    db_session: AsyncSession,
) -> None:
    """The second door into `refund`, and it inherits the same guard.

    `refund_sla_credit` is not a separate money path — it computes an amount and
    delegates — so this test exists to prove the delegation, and would start
    failing the day somebody gives it a path of its own.
    """
    from datetime import timedelta

    _, settled = await a_settled_payment(payments, ordering, gateway)
    clock.advance(timedelta(days=8))
    order = await ordering.refresh_sla_credit(settled.order_id)
    assert order.sla_credit > 0

    with pytest.raises(ConflictError) as excinfo:
        await payments.refund_sla_credit(settled.id, ManualPaymentProvider())

    assert excinfo.value.code == "error.payments.provider_mismatch"
    assert await refund_rows(db_session) == 0
    assert (await payments.get(settled.id)).refunded_amount == Decimal(0)


async def test_the_gateway_that_took_the_money_may_still_refund_it(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    """The guard must not pass by refusing every refund."""
    order, settled = await a_settled_payment(payments, ordering, gateway)

    refunded = await payments.refund(settled.id, gateway)

    assert refunded.status is PaymentStatus.REFUNDED
    assert refunded.refunded_amount == order.total
    assert await refund_rows(db_session) == 1
