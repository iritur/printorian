"""DTOs crossing the payments boundary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.payments.policies import PaymentStatus
from printorian.core.ids import EntityId


class RefundView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    amount: Decimal
    reason: str
    succeeded: bool
    created_at: datetime


class PaymentView(BaseModel):
    """A payment as the cabinet and admin see it.

    Deliberately free of gateway credentials and raw payloads: this crosses to
    clients, and a payment view is not the place to leak provider internals.
    """

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    order_id: EntityId
    provider: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    refunded_amount: Decimal
    confirmation_url: str | None = None
    settled_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime
    refunds: list[RefundView] = Field(default_factory=list)

    @property
    def refundable(self) -> Decimal:
        return self.amount - self.refunded_amount


class PaymentDocument(BaseModel):
    """A receipt or a refund note, derived rather than stored.

    There is no documents table and there should not be one. A receipt *is* a
    settled payment and a refund note *is* a succeeded refund; a second record of
    either would be a second thing that can disagree with the money, and the one
    that disagrees is always the copy.

    ``kind`` is a code, not a caption (ADR-0012): ``receipt`` or ``refund``.
    """

    model_config = ConfigDict(from_attributes=True)

    kind: str
    payment_id: EntityId
    order_id: EntityId
    provider: str
    amount: Decimal
    currency: str
    issued_at: datetime


class StartPayment(BaseModel):
    """Begin collecting for an order.

    Note there is no amount: it is read from the order. A client that can state its
    own price will eventually state a lower one.
    """

    order_id: EntityId
    #: Empty means "whichever gateway this farm is configured for".
    #:
    #: A storefront has no business knowing that a particular deployment settles
    #: through YooKassa — and the previous default, the literal string "mock",
    #: was a live payment page away from being the one a client forgot to
    #: override. The one name a client legitimately sends is `manual`, because
    #: "invoice me" is a decision the customer makes rather than a deployment
    #: setting.
    provider: str = ""
    return_url: str = "https://printorian.local/orders"
    #: The payment method the customer chose, as a gateway method code (e.g.
    #: ``tinkoff_bank`` for T-Pay). Empty means "let the gateway show whatever it
    #: offers". A code the gateway does not know is refused by the gateway — there
    #: is no fallback that would charge the customer a different way than they chose.
    payment_method: str = Field(default="", max_length=40)


class RefundRequest(BaseModel):
    amount: Decimal | None = Field(default=None, description="Omit to refund everything left")
    reason: str = Field(default="refund.requested", max_length=80)
