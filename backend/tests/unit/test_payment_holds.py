"""Money that arrives for an order the farm already cancelled is held for a person.

Security review §5(d), decided by the owner: not refunded automatically, not used
to reinstate the order — held. What is pinned here:

* **the settlement is still recorded**, because refusing it would not give the
  customer's money back, only stop the farm knowing it has it;
* **the order does not move**, and **a hold is written** naming what it was doing;
* **an ordinary settlement holds nothing**;
* **a full refund resolves the hold by itself**, and a partial one does not;
* **a release keeps the money on a named person's word**, once, and only for a
  payment that is actually held.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import OrderingService, OrderStatus
from printorian.contexts.payments import (
    MockPaymentProvider,
    PaymentsService,
    PaymentStatus,
    StartPayment,
    held_payment_count,
)
from printorian.contexts.payments.models import PaymentHold
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from tests.unit.test_payment_lifecycle import HEADERS, an_order, notification, upstream_id


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


async def a_late_settlement(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
):  # type: ignore[no-untyped-def]
    """The customer pays on a page the farm had already given up on."""
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    await ordering.advance(order.id, OrderStatus.CANCELLED, reason="order.cancelled")
    gateway.customer_pays(upstream_id(payment))
    await payments.handle_webhook(
        gateway, HEADERS, notification(upstream_id(payment), "succeeded", order.total, "evt-l")
    )
    return order, payment


async def test_a_late_settlement_is_recorded_and_held_and_moves_nothing(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    order, payment = await a_late_settlement(payments, ordering, gateway)

    assert (await payments.get(payment.id)).status is PaymentStatus.SUCCEEDED
    assert (await ordering.get(order.id)).status is OrderStatus.CANCELLED

    [held] = await payments.held()
    assert held.payment_id == payment.id
    assert held.order_id == order.id
    assert held.order_status == "cancelled"
    assert held.amount == order.total
    assert await held_payment_count(db_session) == 1


async def test_an_ordinary_settlement_holds_nothing(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)
    gateway.customer_pays(upstream_id(payment))
    await payments.handle_webhook(
        gateway, HEADERS, notification(upstream_id(payment), "succeeded", order.total, "evt-o")
    )

    assert (await ordering.get(order.id)).status is OrderStatus.PAID
    assert await payments.held() == []
    assert await held_payment_count(db_session) == 0


async def test_a_redelivered_late_settlement_holds_once(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order, payment = await a_late_settlement(payments, ordering, gateway)
    await payments.handle_webhook(
        gateway, HEADERS, notification(upstream_id(payment), "succeeded", order.total, "evt-l2")
    )

    assert len(await payments.held()) == 1


async def test_a_full_refund_resolves_the_hold_and_a_partial_one_does_not(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    _, payment = await a_late_settlement(payments, ordering, gateway)

    await payments.refund(payment.id, gateway, amount=Decimal("1.00"), reason="refund.held")
    [still] = await payments.held()
    assert still.refunded_amount == Decimal("1.00")

    await payments.refund(payment.id, gateway, reason="refund.held")
    assert await payments.held() == []
    assert await held_payment_count(db_session) == 0


async def test_a_release_keeps_the_money_on_a_named_persons_word(
    payments: PaymentsService,
    ordering: OrderingService,
    gateway: MockPaymentProvider,
    db_session: AsyncSession,
) -> None:
    _, payment = await a_late_settlement(payments, ordering, gateway)
    decider = new_id()

    released = await payments.release_hold(
        payment.id, note="Customer re-ordered by phone", actor_id=None
    )

    assert released.payment_id == payment.id
    assert await payments.held() == []
    hold = await db_session.scalar(select(PaymentHold).where(PaymentHold.payment_id == payment.id))
    assert hold is not None
    assert hold.release_note == "Customer re-ordered by phone"
    assert hold.released_at is not None
    # The money itself is untouched by a release.
    assert (await payments.get(payment.id)).status is PaymentStatus.SUCCEEDED

    with pytest.raises(ConflictError):
        await payments.release_hold(payment.id, note="again", actor_id=decider)


async def test_only_a_held_payment_can_be_released(
    payments: PaymentsService, ordering: OrderingService, gateway: MockPaymentProvider
) -> None:
    order = await an_order(ordering)
    payment = await payments.start(StartPayment(order_id=order.id), gateway)

    with pytest.raises(NotFoundError):
        await payments.release_hold(payment.id, note="nothing to release", actor_id=None)
