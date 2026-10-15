"""Money that arrived for an order not waiting for it: the holding half of `PaymentsService`.

Security review §5(d). A late settlement — the customer paid on a confirmation
page the farm had already given up on, after the order was cancelled — used to be
recorded and then forgotten: the payment read `succeeded`, the order stayed
`cancelled`, and no screen listed the pair. The same happens to a second payment
landing on an order another attempt already paid for.

The owner chose **hold it for a person** over the two automatic answers. Not a
refund, because a gateway refund is irreversible and the customer may have meant
to pay; not reinstating the order, because a cancelled order may be cancelled for
a reason the payment knows nothing about. So the settlement is recorded as it
always was — refusing it would not give the money back — and a `PaymentHold` row
is written in the same unit of work, which is what puts it on
`GET /payments/held` and in the `printorian_payments_held` count.

A hold is resolved in one of two ways, and neither is automatic. A refund through
the ordinary route: a fully refunded payment has nothing left to decide and drops
off the list by itself. Or a release: somebody holding `ISSUE_REFUND` records that
they looked and are keeping it, with their reason. Mixed in like
`refunding.Refunding`, so callers keep one object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select

from printorian.contexts.payments.models import Payment, PaymentHold
from printorian.contexts.payments.policies import PaymentStatus
from printorian.contexts.payments.schemas import HeldPayment
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.ids import EntityId

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from printorian.core.clock import Clock

logger = structlog.get_logger(__name__)

#: The states in which a held payment still has money in it to decide about. A
#: fully refunded one has been decided, by the refund.
_STILL_HELD = (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED)


class Holding:
    """`_hold`, `held` and `release_hold`, for `PaymentsService` to inherit."""

    _db: AsyncSession
    _clock: Clock

    async def _hold(self, payment: Payment, order_status: str) -> None:
        """Record that this settlement moved no order. Called inside `_settle`."""
        self._db.add(PaymentHold(payment_id=payment.id, order_status=order_status))
        await self._db.flush()
        # Loud on purpose, and a warning rather than an error: nothing failed,
        # but money is now waiting for a decision only a person can make.
        logger.warning(
            "payment_held",
            payment_id=str(payment.id),
            order_id=str(payment.order_id),
            order_status=order_status,
        )

    async def held(self) -> list[HeldPayment]:
        """Every hold still waiting for a person, oldest first."""
        rows = await self._db.execute(
            select(Payment, PaymentHold)
            .join(PaymentHold, PaymentHold.payment_id == Payment.id)
            .where(PaymentHold.released_at.is_(None), Payment.status.in_(_STILL_HELD))
            .order_by(PaymentHold.created_at, PaymentHold.id)
        )
        return [_view(payment, hold) for payment, hold in rows.all()]

    async def release_hold(
        self, payment_id: EntityId, *, note: str, actor_id: EntityId | None
    ) -> HeldPayment:
        """Keep the money, on the word of the person releasing it."""
        row = (
            await self._db.execute(
                select(Payment, PaymentHold)
                .join(PaymentHold, PaymentHold.payment_id == Payment.id)
                .where(Payment.id == payment_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("error.payments.hold_not_found", payment_id=str(payment_id))
        payment, hold = row
        if hold.released_at is not None:
            raise ConflictError("error.payments.hold_released", payment_id=str(payment_id))
        hold.released_at = self._clock.now()
        hold.released_by = actor_id
        hold.release_note = note
        await self._db.flush()
        return _view(payment, hold)


async def held_payment_count(db: AsyncSession) -> int:
    """How many holds are waiting for a person — the count `/metrics` publishes.

    A count and never a sum: the scrape is unauthenticated and carries no rubles
    (`api/routers/metrics.py`).
    """
    count = await db.scalar(
        select(func.count())
        .select_from(PaymentHold)
        .join(Payment, Payment.id == PaymentHold.payment_id)
        .where(PaymentHold.released_at.is_(None), Payment.status.in_(_STILL_HELD))
    )
    return int(count or 0)


def _view(payment: Payment, hold: PaymentHold) -> HeldPayment:
    return HeldPayment(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider=payment.provider,
        status=payment.status,
        amount=payment.amount,
        refunded_amount=payment.refunded_amount,
        currency=payment.currency,
        settled_at=payment.settled_at,
        order_status=hold.order_status,
        held_at=hold.created_at,
    )


__all__ = ["Holding", "held_payment_count"]
