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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.ordering import OrderingService, OrderStatus
from printorian.contexts.payments import events as payment_events
from printorian.contexts.payments.models import Payment, PaymentNotification, Refund
from printorian.contexts.payments.policies import (
    PaymentStatus,
    assert_refundable,
    assert_transition,
    reconcile,
)
from printorian.contexts.payments.provider import (
    PaymentProvider,
    PaymentRequest,
    ReceiptLine,
    WebhookEvent,
)
from printorian.contexts.payments.schemas import PaymentDocument, PaymentView, StartPayment
from printorian.core.clock import Clock
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId


class PaymentsService:
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
        live payment returns that one rather than creating a second.
        """
        order = await self._ordering.get(data.order_id)

        existing = await self._db.scalar(
            select(Payment).where(
                Payment.order_id == order.id,
                Payment.status.in_([PaymentStatus.CREATED, PaymentStatus.PENDING]),
            )
        )
        if existing is not None:
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
        self._db.add(payment)
        await self._db.flush()

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
        self, provider: PaymentProvider, headers: dict[str, str], body: bytes
    ) -> PaymentView | None:
        """Process a gateway notification, exactly once.

        Returns ``None`` when the event has already been seen — a duplicate is a
        normal event, not an error, because providers retry by design.
        """
        event = provider.verify_webhook(headers, body)

        if await self._already_seen(provider.name, event, body):
            return None

        payment = await self._db.scalar(
            select(Payment).where(Payment.provider_payment_id == event.provider_payment_id)
        )
        if payment is None:
            raise NotFoundError("error.payments.unknown_payment", payment=event.provider_payment_id)

        # Never settle on the strength of the body. Ask the gateway.
        confirmed = await provider.fetch(event.provider_payment_id)
        if confirmed.status is not PaymentStatus.SUCCEEDED:
            return await self._view(payment.id)

        return await self._settle(payment, confirmed.amount)

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
        return await self._settle(payment, event.amount, actor_id=actor_id)

    async def _settle(
        self, payment: Payment, captured: Decimal, *, actor_id: EntityId | None = None
    ) -> PaymentView:
        if payment.status is PaymentStatus.SUCCEEDED:
            return await self._view(payment.id)

        # Refuse a settlement that does not match what was owed, in either direction.
        reconcile(payment.amount, captured)
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

        await self._bus.publish(
            payment_events.PaymentSettled(
                payment_id=payment.id, order_id=payment.order_id, amount=str(payment.amount)
            )
        )
        return await self._view(payment.id)

    # -- refunding -------------------------------------------------------

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

    # -- reading ---------------------------------------------------------

    async def get(self, payment_id: EntityId) -> PaymentView:
        return await self._view(payment_id)

    async def for_order(self, order_id: EntityId) -> list[PaymentView]:
        payments = await self._db.scalars(
            select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at)
        )
        return [await self._view(payment.id) for payment in payments]

    # -- internals -------------------------------------------------------

    async def documents_for(self, order_ids: Sequence[EntityId]) -> list[PaymentDocument]:
        """Receipts and refund notes for a set of orders, newest first.

        Takes ids rather than a customer: payments know about orders and nothing
        about who placed them, and teaching this context to filter by customer
        would be teaching it to read another context's table. The caller scopes.

        Only settled payments and only succeeded refunds. A payment that was
        started and abandoned is not a receipt, and listing it as one would put a
        document in front of a customer for money that never moved.
        """
        if not order_ids:
            return []

        payments = await self._db.scalars(
            select(Payment)
            .where(Payment.order_id.in_(list(order_ids)))
            .options(selectinload(Payment.refunds))
        )

        documents: list[PaymentDocument] = []
        for payment in payments:
            if payment.status is PaymentStatus.SUCCEEDED and payment.settled_at is not None:
                documents.append(
                    PaymentDocument(
                        kind="receipt",
                        payment_id=payment.id,
                        order_id=payment.order_id,
                        provider=payment.provider,
                        amount=payment.amount,
                        currency=payment.currency,
                        issued_at=payment.settled_at,
                    )
                )
            documents.extend(
                PaymentDocument(
                    kind="refund",
                    payment_id=payment.id,
                    order_id=payment.order_id,
                    provider=payment.provider,
                    amount=refund.amount,
                    currency=payment.currency,
                    issued_at=refund.created_at,
                )
                for refund in payment.refunds
                if refund.succeeded
            )

        documents.sort(key=lambda row: row.issued_at, reverse=True)
        return documents

    async def _already_seen(self, provider_name: str, event: WebhookEvent, body: bytes) -> bool:
        """Record the notification, reporting whether it is a repeat.

        The event key is the provider's own id when present, otherwise a digest of
        the body — so a provider that omits ids still cannot be replayed.
        """
        key = event.event_id or hashlib.sha256(body).hexdigest()
        duplicate = await self._db.scalar(
            select(PaymentNotification).where(
                PaymentNotification.provider == provider_name,
                PaymentNotification.event_key == key,
            )
        )
        if duplicate is not None:
            return True

        self._db.add(
            PaymentNotification(
                provider=provider_name,
                event_key=key,
                status=event.status.value,
                payload=json.loads(body) if body else {},
            )
        )
        await self._db.flush()
        return False

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
