"""In-memory payment gateway, for tests and local development.

**Refuses to load in production**, exactly like the mock printer driver (ADR-0007).
A simulated gateway reachable from a real deployment is a way to mark orders paid
without money moving, so the guard is structural rather than a naming convention.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from printorian.contexts.payments.policies import PaymentStatus
from printorian.contexts.payments.provider import (
    PaymentProviderError,
    PaymentRequest,
    ProviderPayment,
    ProviderRefund,
    WebhookEvent,
    WebhookVerificationError,
)
from printorian.core.config import Settings
from printorian.core.errors import ConfigurationError

#: Header the mock uses in place of a real signature, so tests exercise the
#: "verify before trusting" path rather than skipping it.
SIGNATURE_HEADER = "x-mock-signature"
VALID_SIGNATURE = "trusted-mock"


@dataclass(slots=True)
class _Record:
    payment: ProviderPayment
    refunded: Decimal = Decimal(0)


@dataclass(slots=True)
class MockPaymentProvider:
    """A gateway that does what it is told, deterministically."""

    settings: Settings
    #: Make ``create`` fail, to exercise the unhappy path.
    fail_create: bool = False
    _records: dict[str, _Record] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.settings.is_production:
            raise ConfigurationError(
                "error.payments.mock_in_production",
                hint="The mock gateway exists for tests and local development only.",
            )

    @property
    def name(self) -> str:
        return "mock"

    async def create(self, request: PaymentRequest) -> ProviderPayment:
        if self.fail_create:
            raise PaymentProviderError("error.payments.provider", reason="injected")

        # Same idempotency key returns the same payment, as a real gateway does.
        existing = self._records.get(request.idempotency_key)
        if existing is not None:
            return existing.payment

        payment = ProviderPayment(
            provider_payment_id=f"mock-{request.idempotency_key}",
            status=PaymentStatus.PENDING,
            amount=request.amount,
            currency=request.currency,
            confirmation_url=f"https://mock.gateway.local/pay/{request.idempotency_key}",
            raw={"order_number": request.order_number},
        )
        self._records[request.idempotency_key] = _Record(payment=payment)
        return payment

    async def fetch(self, provider_payment_id: str) -> ProviderPayment:
        for record in self._records.values():
            if record.payment.provider_payment_id == provider_payment_id:
                return record.payment

        raise PaymentProviderError("error.payments.not_found_upstream", payment=provider_payment_id)

    async def refund(
        self, provider_payment_id: str, amount: Decimal, *, idempotency_key: str
    ) -> ProviderRefund:
        record = self._record_for(provider_payment_id)
        record.refunded += amount
        return ProviderRefund(
            refund_id=f"mock-refund-{idempotency_key}",
            provider_payment_id=provider_payment_id,
            amount=amount,
            succeeded=True,
        )

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        lowered = {key.lower(): value for key, value in headers.items()}
        if lowered.get(SIGNATURE_HEADER) != VALID_SIGNATURE:
            raise WebhookVerificationError("error.payments.webhook_unverified")

        payload = json.loads(body)
        return WebhookEvent(
            provider_payment_id=payload["payment_id"],
            status=PaymentStatus(payload["status"]),
            amount=Decimal(str(payload["amount"])),
            event_id=payload.get("event_id", ""),
            raw=payload,
        )

    # -- test helpers ----------------------------------------------------

    def customer_pays(self, provider_payment_id: str) -> None:
        """Simulate the customer completing payment at the gateway.

        This mutates the *upstream* record, which is what makes the mock faithful:
        the service confirms every settlement by re-reading the provider, so a
        webhook alone must not be enough to move money. A mock whose ``fetch``
        never reflected payment would let that rule pass untested.
        """
        record = self._record_for(provider_payment_id)
        record.payment = ProviderPayment(
            provider_payment_id=record.payment.provider_payment_id,
            status=PaymentStatus.SUCCEEDED,
            amount=record.payment.amount,
            currency=record.payment.currency,
            confirmation_url=record.payment.confirmation_url,
            raw=record.payment.raw,
        )

    def settle(self, provider_payment_id: str, amount: Decimal | None = None) -> WebhookEvent:
        """Build the notification a successful payment would produce."""
        record = self._record_for(provider_payment_id)
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=PaymentStatus.SUCCEEDED,
            amount=amount if amount is not None else record.payment.amount,
            event_id=f"evt-{provider_payment_id}",
        )

    def _record_for(self, provider_payment_id: str) -> _Record:
        for record in self._records.values():
            if record.payment.provider_payment_id == provider_payment_id:
                return record

        raise PaymentProviderError("error.payments.not_found_upstream", payment=provider_payment_id)
