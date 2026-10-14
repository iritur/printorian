"""Returning money: the refunding half of `PaymentsService`, mixed into it.

Split out of `service.py` when the gateway-lifecycle fixes from the security
review's second pass (#27) took that file past the 400-line gate, and split
**here** because refunding is the one job in the service that only ever sends
money out. Starting and settling bring money in and share the notification and
reconciliation machinery; a refund shares none of it. The shape is
`settings.resolvers`: a mixin, so callers keep one object and one import.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from printorian.contexts.payments import events as payment_events
from printorian.contexts.payments.models import Payment, Refund
from printorian.contexts.payments.policies import (
    PaymentStatus,
    assert_refundable,
    assert_transition,
)
from printorian.contexts.payments.provider import PaymentProvider
from printorian.contexts.payments.schemas import PaymentView
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.ids import EntityId

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from printorian.contexts.ordering import OrderingService
    from printorian.core.events import EventBus


class Refunding:
    """`refund` and `refund_sla_credit`, for `PaymentsService` to inherit.

    The attributes are declared, not set: `PaymentsService.__init__` supplies them,
    and annotating them here is what lets `mypy --strict` check this half on its
    own without importing the class that mixes it in.
    """

    _db: AsyncSession
    _bus: EventBus
    _ordering: OrderingService
    _view: Callable[[EntityId], Awaitable[PaymentView]]

    async def refund(
        self,
        payment_id: EntityId,
        provider: PaymentProvider,
        *,
        amount: Decimal | None = None,
        reason: str = "refund.requested",
    ) -> PaymentView:
        """Return money, in whole or in part.

        Partial refunds are how an SLA credit is actually paid back to a customer
        who has already been charged (ADR-0013 / the scenario's late-delivery rule).
        """
        payment = await self._db.get(Payment, payment_id)
        if payment is None:
            raise NotFoundError("error.payments.not_found", payment_id=str(payment_id))

        # Money only goes back through the gateway that took it. The route above
        # already resolves the gateway from the payment rather than from the
        # caller; this is the second, independent guard — the same two-guard shape
        # `api/providers.py` uses to keep the mock out of production, and for the
        # same reason: what a single missed check costs here is a payment marked
        # refunded while the money is still at a gateway nobody told.
        #
        # `ManualPaymentProvider` is the one that makes this urgent. Its refund
        # always reports success, correctly — its contract is that a person will
        # send the money — so routing a card payment into it produces a refund
        # record for a transfer nobody is going to make.
        if provider.name != payment.provider:
            raise ConflictError(
                "error.payments.provider_mismatch",
                expected=payment.provider,
                actual=provider.name,
            )

        if not payment.status.is_settled:
            raise ConflictError("error.payments.not_settled", status=payment.status.value)

        requested = amount if amount is not None else payment.refundable
        assert_refundable(
            settled=payment.amount, already_refunded=payment.refunded_amount, amount=requested
        )

        # Counted with a query rather than len(payment.refunds): the relationship is
        # not eager-loaded here, and touching a lazy collection under asyncio raises
        # MissingGreenlet.
        sequence = (
            int(
                await self._db.scalar(
                    select(func.count()).select_from(Refund).where(Refund.payment_id == payment.id)
                )
                or 0
            )
            + 1
        )
        result = await provider.refund(
            payment.provider_payment_id,
            requested,
            currency=payment.currency,
            idempotency_key=f"{payment.idempotency_key}-r{sequence}",
        )

        self._db.add(
            Refund(
                payment_id=payment.id,
                sequence=sequence,
                provider_refund_id=result.refund_id,
                amount=requested,
                reason=reason,
                succeeded=result.succeeded,
            )
        )
        if result.succeeded:
            payment.refunded_amount += requested
            target = (
                PaymentStatus.REFUNDED
                if payment.refunded_amount >= payment.amount
                else PaymentStatus.PARTIALLY_REFUNDED
            )
            assert_transition(payment.status, target)
            payment.status = target

        await self._db.flush()
        await self._bus.publish(
            payment_events.PaymentRefunded(
                payment_id=payment.id,
                order_id=payment.order_id,
                amount=str(requested),
                reason=reason,
            )
        )
        return await self._view(payment.id)

    async def refund_sla_credit(
        self, payment_id: EntityId, provider: PaymentProvider
    ) -> PaymentView:
        """Return exactly what lateness owes, and no more."""
        payment = await self._db.get(Payment, payment_id)
        if payment is None:
            raise NotFoundError("error.payments.not_found", payment_id=str(payment_id))

        order = await self._ordering.get(payment.order_id)
        outstanding = order.sla_credit - payment.refunded_amount
        if outstanding <= 0:
            return await self._view(payment.id)
        return await self.refund(
            payment_id, provider, amount=outstanding, reason="refund.sla_credit"
        )


__all__ = ["Refunding"]
