"""Payment use cases: start, settle, refund.

The settlement path is written around one rule: **a webhook is a hint, not a fact.**
Every notification is verified by the provider adapter, recorded for idempotency,
and then confirmed by re-reading the payment from the gateway before any money is
believed. YooKassa does not sign its notifications at all, so a body that says
"succeeded" is worth exactly nothing on its own.

Marking the order paid happens in the same transaction as recording the settlement.
Doing it by published event would let the payment land while the order silently
stayed unpaid, because the event bus deliberately swallows handler failures.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.ordering import OrderingService, OrderStatus
from printorian.contexts.payments import events as payment_events
from printorian.contexts.payments.documents import documents_for
from printorian.contexts.payments.holding import Holding
from printorian.contexts.payments.models import Payment, PaymentNotification
from printorian.contexts.payments.policies import (
    GATEWAY_CLOSED,
    PaymentStatus,
    assert_transition,
    reconcile,
    reconcile_currency,
)
from printorian.contexts.payments.provider import (
    PaymentProvider,
    PaymentProviderError,
    PaymentRequest,
    ReceiptLine,
    WebhookEvent,
)
from printorian.contexts.payments.refunding import Refunding
from printorian.contexts.payments.schemas import PaymentDocument, PaymentView, StartPayment
from printorian.core.clock import Clock
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId

#: `ManualPaymentProvider.name`. Spelled here rather than imported, because the
#: adapters import this module's schemas and the other direction would be a cycle.
MANUAL_PROVIDER = "manual"


class PaymentsService(Refunding, Holding):
    """Collecting and returning money for orders."""

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        bus: EventBus,
        ordering: OrderingService,
    ) -> None:
        self._db = session
        self._clock = clock
        self._bus = bus
        self._ordering = ordering

    # -- starting --------------------------------------------------------

    async def start(self, data: StartPayment, provider: PaymentProvider) -> PaymentView:
        """Register a payment for an order and return where to pay.

        The amount is taken from the order. Re-starting an order that already has a
        live payment returns that one rather than creating a second — **unless the
        gateway has given up on it**, which is asked rather than assumed (see
        `_still_live`). A payment the gateway cancelled used to be handed back for
        ever, with a confirmation page that no longer took money.
        """
        order = await self._ordering.get(data.order_id)

        existing = await self._live_payment(order.id)
        if existing is not None and await self._still_live(existing, provider):
            return await self._view(existing.id)

        if order.status.is_paid:
            raise ConflictError("error.payments.order_already_paid", order=order.number)

        payable = order.payable_now
        payment = Payment(
            order_id=order.id,
            provider=provider.name,
            idempotency_key=f"{order.number}-{secrets.token_hex(8)}",
            status=PaymentStatus.CREATED,
            amount=payable,
            currency=order.currency,
        )
        try:
            # A savepoint, so losing the race below rolls back this one insert and
            # not the caller's whole unit of work.
            async with self._db.begin_nested():
                self._db.add(payment)
                await self._db.flush()
        except IntegrityError:
            # Another request for the same order inserted its live payment between
            # our check and our insert — a double click. `uq_payments_one_live_per_
            # order` refused this one **before the gateway was called**, so there is
            # one payment upstream, not two. Hand back the winner.
            winner = await self._live_payment(order.id)
            if winner is None:
                raise
            return await self._view(winner.id)

        registered = await provider.create(
            PaymentRequest(
                order_number=order.number,
                amount=payable,
                currency=order.currency,
                description=f"Printorian {order.number}",
                return_url=data.return_url,
                idempotency_key=payment.idempotency_key,
                customer_email=order.customer_email,
                payment_method_type=data.payment_method or None,
                receipt=(
                    ReceiptLine(
                        description=f"3D printing, order {order.number}",
                        quantity=Decimal(1),
                        amount=payable,
                    ),
                ),
            )
        )

        payment.provider_payment_id = registered.provider_payment_id
        payment.confirmation_url = registered.confirmation_url
        assert_transition(payment.status, PaymentStatus.PENDING)
        payment.status = PaymentStatus.PENDING
        await self._db.flush()

        if order.status is OrderStatus.DRAFT:
            await self._ordering.advance(
                order.id, OrderStatus.AWAITING_PAYMENT, reason="payment.started"
            )

        await self._bus.publish(
            payment_events.PaymentStarted(
                payment_id=payment.id, order_id=order.id, amount=str(payable)
            )
        )
        return await self._view(payment.id)

    # -- settling --------------------------------------------------------

    async def handle_webhook(
        self,
        provider: PaymentProvider,
        headers: dict[str, str],
        body: bytes,
        *,
        source: str = "",
    ) -> PaymentView | None:
        """Process a gateway notification, exactly once.

        Returns ``None`` when the event has already been seen — a duplicate is a
        normal event, not an error, because providers retry by design.
        """
        event = provider.verify_webhook(headers, body, source=source)

        notification = await self._record_notification(provider.name, event, body)
        if notification is None:
            return None

        payment = await self._db.scalar(
            select(Payment).where(Payment.provider_payment_id == event.provider_payment_id)
        )
        if payment is None:
            raise NotFoundError("error.payments.unknown_payment", payment=event.provider_payment_id)
        # Linked, so the runbook's reconciliation can join what the gateway sent to
        # the payment it was about. The column and its index existed from the
        # start and were never written.
        notification.payment_id = payment.id

        # Never settle on the strength of the body. Ask the gateway.
        confirmed = await provider.fetch(event.provider_payment_id)
        if confirmed.status in GATEWAY_CLOSED:
            await self._close(payment, confirmed.status)
            return await self._view(payment.id)
        if confirmed.status is not PaymentStatus.SUCCEEDED:
            return await self._view(payment.id)

        return await self._settle(payment, confirmed.amount, currency=confirmed.currency)

    async def settle_manually(
        self, payment_id: EntityId, event: WebhookEvent, *, actor_id: EntityId | None = None
    ) -> PaymentView:
        """Record an operator-confirmed settlement (bank transfer, cash).

        Takes the same shape as a gateway notification so both run the same
        settlement routine — one path, not two that can drift apart.
        """
        payment = await self._db.get(Payment, payment_id)
        if payment is None:
            raise NotFoundError("error.payments.not_found", payment_id=str(payment_id))
        # Only a payment the *manual* provider owns. The route is a manager's
        # (`VIEW_FINANCIALS`), and until this check it would mark a pending card
        # payment succeeded and the order paid with no money moved — the hole the
        # refund path closed with the same guard, still open on the way in.
        if payment.provider != MANUAL_PROVIDER:
            raise ConflictError(
                "error.payments.provider_mismatch",
                expected=MANUAL_PROVIDER,
                actual=payment.provider,
            )
        return await self._settle(payment, event.amount, actor_id=actor_id)

    async def _settle(
        self,
        payment: Payment,
        captured: Decimal,
        *,
        currency: str | None = None,
        actor_id: EntityId | None = None,
    ) -> PaymentView:
        if payment.status is PaymentStatus.SUCCEEDED:
            return await self._view(payment.id)

        # Refuse a settlement that does not match what was owed, in either direction
        # and in either unit. ``currency`` is what the gateway reports it captured;
        # an operator's manual confirmation carries none and is in the payment's own.
        reconcile(payment.amount, captured)
        if currency is not None:
            reconcile_currency(payment.currency, currency)
        assert_transition(payment.status, PaymentStatus.SUCCEEDED)

        payment.status = PaymentStatus.SUCCEEDED
        payment.settled_at = self._clock.now()
        await self._db.flush()

        # Same transaction as the settlement: the money and the order state move
        # together or not at all.
        order = await self._ordering.get(payment.order_id)
        if order.status is OrderStatus.AWAITING_PAYMENT:
            await self._ordering.advance(
                order.id, OrderStatus.PAID, reason="payment.settled", actor_id=actor_id
            )
        else:
            # The money is real and the order was not waiting for it — cancelled,
            # or paid by another attempt. Held for a person, in this same unit of
            # work, rather than accepted in silence (`holding`).
            await self._hold(payment, order.status.value)

        await self._bus.publish(
            payment_events.PaymentSettled(
                payment_id=payment.id, order_id=payment.order_id, amount=str(payment.amount)
            )
        )
        return await self._view(payment.id)

    # -- reading ---------------------------------------------------------

    async def get(self, payment_id: EntityId) -> PaymentView:
        return await self._view(payment_id)

    async def for_order(self, order_id: EntityId) -> list[PaymentView]:
        payments = await self._db.scalars(
            # Single-term on purpose (`core.pagination`): every payment for the
            # order comes back and a tie swaps two rows without moving a total.
            select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at)
        )
        return [await self._view(payment.id) for payment in payments]

    # -- internals -------------------------------------------------------

    async def documents_for(self, order_ids: Sequence[EntityId]) -> list[PaymentDocument]:
        """Receipts and refund notes for these orders — see `payments.documents`.

        Kept on the service so the context's public surface stays one object: the
        derivation lives next door because it is a different job (reading what the
        money did, never moving it), not because this file ran out of room.
        """
        return await documents_for(self._db, order_ids)

    async def _live_payment(self, order_id: EntityId) -> Payment | None:
        """The order's one payment still awaiting money, if it has one."""
        return await self._db.scalar(
            select(Payment).where(
                Payment.order_id == order_id,
                Payment.status.in_([PaymentStatus.CREATED, PaymentStatus.PENDING]),
            )
        )

    async def _still_live(self, payment: Payment, provider: PaymentProvider) -> bool:
        """Ask the gateway whether a pending payment can still be paid.

        The webhook that would have told us is not guaranteed to arrive — that is
        what `PaymentProvider.fetch` says it is for. One GET per checkout reload
        is the price, and it buys two recoveries: a payment the gateway cancelled
        is recorded so a fresh one can be started, and one it *settled* while the
        notification was lost is settled here instead of waiting for a person.

        Only for the gateway that owns the payment, and never for `manual`, whose
        "upstream" is a person. If the gateway cannot be reached the answer is the
        old one — hand back what we have — because refusing checkout during an
        outage would turn a gateway's bad minute into the farm's.
        """
        if (
            payment.status is not PaymentStatus.PENDING
            or payment.provider != provider.name
            or payment.provider == MANUAL_PROVIDER
            or not payment.provider_payment_id
        ):
            return True
        try:
            upstream = await provider.fetch(payment.provider_payment_id)
        except PaymentProviderError:
            return True
        if upstream.status in GATEWAY_CLOSED:
            await self._close(payment, upstream.status)
            return False
        if upstream.status is PaymentStatus.SUCCEEDED:
            await self._settle(payment, upstream.amount, currency=upstream.currency)
        return True

    async def _close(self, payment: Payment, status: PaymentStatus) -> None:
        """Record that the gateway will never take money for this payment.

        Only from a state still awaiting money. A cancellation reported for a
        payment already settled is not acted on — money states do not move
        backwards on a notification — and the order is left where it is, so the
        customer can start again.
        """
        if payment.status not in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
            return
        assert_transition(payment.status, status)
        payment.status = status
        payment.failure_reason = f"gateway.{status.value}"
        await self._db.flush()

    async def _record_notification(
        self, provider_name: str, event: WebhookEvent, body: bytes
    ) -> PaymentNotification | None:
        """Record the notification, or ``None`` when it is a repeat.

        The event key is the provider's own id when present, otherwise a digest of
        the body — so a provider that omits ids still cannot be replayed. The row
        is returned so the caller can link it to the payment once it has found it.
        """
        key = event.event_id or hashlib.sha256(body).hexdigest()
        duplicate = await self._db.scalar(
            select(PaymentNotification).where(
                PaymentNotification.provider == provider_name,
                PaymentNotification.event_key == key,
            )
        )
        if duplicate is not None:
            return None

        notification = PaymentNotification(
            provider=provider_name,
            event_key=key,
            status=event.status.value,
            payload=json.loads(body) if body else {},
        )
        self._db.add(notification)
        await self._db.flush()
        return notification

    async def _view(self, payment_id: EntityId) -> PaymentView:
        payment = await self._db.scalar(
            select(Payment)
            .options(selectinload(Payment.refunds))
            .execution_options(populate_existing=True)
            .where(Payment.id == payment_id)
        )
        if payment is None:
            raise NotFoundError("error.payments.not_found", payment_id=str(payment_id))
        return PaymentView.model_validate(payment)
