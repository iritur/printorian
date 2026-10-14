"""The one reconciliation gap the database can hold: the gateway said paid, we did not.

INFRASTRUCTURE §5's `printorian_payment_notifications_unreconciled`, defined
here because ADR-0019 names the reason for it — `payment_notifications` is the
record of what the gateway actually told us — without saying what "unreconciled"
counts.

**What it cannot count, first, because that is what makes the definition
narrow.** A notification whose settlement is *refused* — a wrong amount, a wrong
currency — rolls back with the notification row (`PaymentsService.handle_webhook`
records both in one unit of work), and the gateway retries it. So a refused
settlement leaves nothing in the table to count; its signal is the webhook's
4xx in the access log, which is where an alert on it belongs.

**What it does count:** payments for which the gateway sent a notification
saying `succeeded`, and which this side still does not hold as settled. That
happens when the re-read from the gateway answered "not yet" — the body and the
gateway disagreed for a moment — and stays non-zero only when nothing
afterwards settled it: no later notification, no customer reopening checkout
(`PaymentsService._still_live`). Transiently non-zero is the gateway being slow;
persistently non-zero is money the customer has paid and the farm has not
booked.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.payments.models import Payment, PaymentNotification
from printorian.contexts.payments.policies import PaymentStatus

#: Where a payment is once its money is on our books, refunded or not.
_BOOKED = (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)


async def unreconciled_payments(db: AsyncSession) -> int:
    """Payments the gateway reported paid that this side has not settled."""
    count = await db.scalar(
        select(func.count(func.distinct(PaymentNotification.payment_id)))
        .join(Payment, Payment.id == PaymentNotification.payment_id)
        .where(
            PaymentNotification.status == PaymentStatus.SUCCEEDED.value,
            Payment.status.not_in(_BOOKED),
        )
    )
    return int(count or 0)


__all__ = ["unreconciled_payments"]
