"""Manual settlement — an operator confirms the money arrived.

For bank transfers, cash at the counter, and B2B invoices. Not a stub and not a
placeholder: plenty of a Russian farm's revenue arrives this way, and pretending
otherwise would push operators into faking card payments to make the software work.

Like the ``manual`` printer driver, it is honest about what it is. It never invents
a confirmation: nothing is settled until a human with ``ISSUE_REFUND``-level
authority says so, and that act is recorded against their name.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from printorian.contexts.payments.policies import PaymentStatus
from printorian.contexts.payments.provider import (
    PaymentProviderError,
    PaymentRequest,
    ProviderPayment,
    ProviderRefund,
    WebhookEvent,
    WebhookVerificationError,
)


class ManualPaymentProvider:
    """Settlement recorded by a person rather than a gateway."""

    @property
    def name(self) -> str:
        return "manual"

    async def create(self, request: PaymentRequest) -> ProviderPayment:
        """Register the expectation. Nothing is collected here.

        There is no confirmation URL: the customer is not sent anywhere, because a
        human is going to reconcile a bank statement instead.
        """
        return ProviderPayment(
            provider_payment_id=f"manual-{request.idempotency_key}",
            status=PaymentStatus.PENDING,
            amount=request.amount,
            currency=request.currency,
            confirmation_url=None,
            raw={"order_number": request.order_number},
        )

    async def fetch(self, provider_payment_id: str) -> ProviderPayment:
        """There is no upstream to ask.

        Raising is deliberate. Returning an invented "still pending" would let a
        reconciliation job believe it had checked something when it had not — the
        exact failure that let V1's printer connector look alive while doing nothing.
        """
        raise PaymentProviderError(
            "error.payments.manual_has_no_upstream", payment=provider_payment_id
        )

    async def refund(
        self, provider_payment_id: str, amount: Decimal, *, idempotency_key: str
    ) -> ProviderRefund:
        """Record the intent to refund; a person actually sends the money."""
        return ProviderRefund(
            refund_id=f"manual-refund-{idempotency_key}",
            provider_payment_id=provider_payment_id,
            amount=amount,
            succeeded=True,
            raw={"requires_human_transfer": True},
        )

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        """Manual payments have no webhooks, so any arriving is suspect."""
        raise WebhookVerificationError("error.payments.manual_has_no_webhook")

    # -- the human-facing half -------------------------------------------

    @staticmethod
    def settlement(
        provider_payment_id: str, amount: Decimal, *, reference: str = ""
    ) -> WebhookEvent:
        """Build the event an operator's confirmation produces.

        Shaped exactly like a gateway notification so the service handles both by
        the same path — one settlement routine, not two that can drift apart.
        """
        raw: dict[str, Any] = {"confirmed_by": "operator", "reference": reference}
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=PaymentStatus.SUCCEEDED,
            amount=amount,
            event_id=f"manual-settle-{provider_payment_id}",
            raw=raw,
        )
