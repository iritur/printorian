"""Payments — collecting and returning money.

Public interface. The gateway is pluggable (ADR-0009); nothing outside this package
knows which one is configured.

Two rules define this context:

* the amount always comes from the order, never from a request;
* a webhook is a hint — settlement is confirmed by re-reading the gateway.
"""

from printorian.contexts.payments.policies import (
    RECONCILIATION_TOLERANCE,
    PaymentStatus,
    assert_refundable,
    can_transition,
    reconcile,
)
from printorian.contexts.payments.provider import (
    PaymentProvider,
    PaymentProviderError,
    PaymentRequest,
    ProviderPayment,
    ProviderRefund,
    ReceiptLine,
    WebhookEvent,
    WebhookVerificationError,
    available,
    get,
    register,
    reset_registry,
)
from printorian.contexts.payments.providers import ManualPaymentProvider, MockPaymentProvider
from printorian.contexts.payments.schemas import (
    PaymentView,
    RefundRequest,
    RefundView,
    StartPayment,
)
from printorian.contexts.payments.service import PaymentsService

__all__ = [
    "RECONCILIATION_TOLERANCE",
    "ManualPaymentProvider",
    "MockPaymentProvider",
    "PaymentProvider",
    "PaymentProviderError",
    "PaymentRequest",
    "PaymentStatus",
    "PaymentView",
    "PaymentsService",
    "ProviderPayment",
    "ProviderRefund",
    "ReceiptLine",
    "RefundRequest",
    "RefundView",
    "StartPayment",
    "WebhookEvent",
    "WebhookVerificationError",
    "assert_refundable",
    "available",
    "can_transition",
    "get",
    "reconcile",
    "register",
    "reset_registry",
]
