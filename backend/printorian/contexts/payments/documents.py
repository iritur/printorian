"""Receipts and refund notes, derived from the money rather than stored.

Split out of `service.py`, which is about *moving* money — start, settle, refund —
where this is about what a customer is later shown to prove it moved. They read
the same table and share nothing else: nothing here writes, and no rule here can
change what a payment does.

There is no documents table and there should not be one. A receipt *is* a settled
payment and a refund note *is* a succeeded refund; a second record of either would
be a second thing that can disagree with the money, and the one that disagrees is
always the copy.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.payments.models import Payment
from printorian.contexts.payments.policies import PaymentStatus
from printorian.contexts.payments.schemas import PaymentDocument
from printorian.core.ids import EntityId


async def documents_for(db: AsyncSession, order_ids: Sequence[EntityId]) -> list[PaymentDocument]:
    """Receipts and refund notes for a set of orders, newest first.

    Takes ids rather than a customer: payments know about orders and nothing about
    who placed them, and teaching this context to filter by customer would be
    teaching it to read another context's table. The caller scopes — and since the
    payments *routes* were found not to, that sentence is now enforced by
    `api/routers/_order_access.py` rather than merely written down here.

    Only settled payments and only succeeded refunds. A payment that was started
    and abandoned is not a receipt, and listing it as one would put a document in
    front of a customer for money that never moved.
    """
    if not order_ids:
        return []

    payments = await db.scalars(
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
