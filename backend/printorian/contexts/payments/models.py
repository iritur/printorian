"""Persistent models for payments."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.payments.policies import PaymentStatus
from printorian.core.db import Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class Payment(Entity):
    """One attempt to collect money for an order."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        Index("ix_payments_order_id_status", "order_id", "status"),
        Index("ix_payments_provider_payment_id", "provider_payment_id"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint("refunded_amount >= 0", name="refunded_non_negative"),
        # The one invariant in this schema that is worth money: without it a bug in
        # the refund path, or one hand-written UPDATE during an incident, can send
        # back more than was ever collected and nothing in the database objects.
        CheckConstraint("refunded_amount <= amount", name="refunded_within_amount"),
    )

    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Ours. Sent to the gateway so a retried create returns the same payment.
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The gateway's. Empty until it has been registered upstream.
    provider_payment_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    status: Mapped[PaymentStatus] = mapped_column(
        enum_column(PaymentStatus), nullable=False, default=PaymentStatus.CREATED
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    refunded_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal(0)
    )

    confirmation_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    refunds: Mapped[list[Refund]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", order_by="Refund.sequence"
    )
    notifications: Mapped[list[PaymentNotification]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )

    @property
    def refundable(self) -> Decimal:
        return self.amount - self.refunded_amount


class Refund(Entity):
    """Money sent back, in whole or in part."""

    __tablename__ = "refunds"
    __table_args__ = (
        # PostgreSQL does not index a foreign key for you. Without this, listing a
        # payment's refunds — and cascading a payment delete — is a sequential scan.
        Index("ix_refunds_payment_id", "payment_id"),
        UniqueConstraint("payment_id", "sequence", name="uq_refunds_payment_id_sequence"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    payment_id: Mapped[EntityId] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False, default=1)
    provider_refund_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    #: Machine-readable cause, e.g. ``refund.sla_credit`` or ``refund.cancelled``.
    reason: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    succeeded: Mapped[bool] = mapped_column(nullable=False, default=False)

    payment: Mapped[Payment] = relationship(back_populates="refunds")


class PaymentNotification(Entity):
    """Every gateway notification, recorded once.

    The unique constraint is the idempotency mechanism: a provider redelivering the
    same event hits a duplicate key and the handler returns without acting twice.
    Storing the raw body also means a disputed settlement can be reconstructed from
    what the gateway actually sent, not from what we remember it sending.
    """

    __tablename__ = "payment_notifications"
    __table_args__ = (
        UniqueConstraint(
            "provider", "event_key", name="uq_payment_notifications_provider_event_key"
        ),
        Index("ix_payment_notifications_payment_id", "payment_id"),
    )

    payment_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Provider event id where given, otherwise a digest of the payload.
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)

    payment: Mapped[Payment | None] = relationship(back_populates="notifications")
