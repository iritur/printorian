"""The payment provider contract.

One of the three genuinely pluggable extension points (ADR-0009). Nothing outside
this package knows which gateway is in use, so swapping YooKassa for CloudPayments
touches the registry and one module.

The interface is deliberately narrow — create, read, refund, verify a webhook. Any
provider that cannot do those four things cannot be used to take money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from printorian.contexts.payments.policies import PaymentStatus
from printorian.core.errors import ConfigurationError, IntegrationError


class PaymentProviderError(IntegrationError):
    """The gateway failed or refused."""

    code = "error.payments.provider"


class WebhookVerificationError(PaymentProviderError):
    """A notification could not be proven to come from the provider.

    Treated as hostile, never as "probably fine": an unauthenticated webhook that
    marks orders paid is a way to get free prints.
    """

    code = "error.payments.webhook_unverified"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiptLine:
    """One line of a fiscal receipt (54-ФЗ).

    Russian gateways forward these to the fiscal operator, so the shape is part of
    the payment request rather than an afterthought.
    """

    description: str
    quantity: Decimal
    amount: Decimal
    vat_code: int = 1  # 1 = VAT not applicable; set per the farm's tax regime


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentRequest:
    """What the farm asks the gateway to collect.

    ``amount`` is always taken from the order, never from a client request.
    """

    order_number: str
    amount: Decimal
    currency: str
    description: str
    return_url: str
    #: Deduplicates retries at the provider. Same key, same payment — never two.
    idempotency_key: str
    customer_email: str = ""
    receipt: tuple[ReceiptLine, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    #: The gateway's own method code, e.g. YooKassa ``tinkoff_bank`` for T-Pay.
    #: ``None`` means "whatever the gateway offers" — the customer picks on the
    #: payment page. Set it when the customer chose a specific method first, so the
    #: gateway opens that method's flow rather than a menu.
    payment_method_type: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderPayment:
    """The gateway's view of a payment."""

    provider_payment_id: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    #: Where to send the customer to pay. Absent for manual settlement.
    confirmation_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderRefund:
    refund_id: str
    provider_payment_id: str
    amount: Decimal
    succeeded: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookEvent:
    """A verified notification from the gateway."""

    provider_payment_id: str
    status: PaymentStatus
    amount: Decimal
    #: Provider's own event id where available — the key for idempotency.
    event_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """What every gateway adapter must implement."""

    @property
    def name(self) -> str: ...

    async def create(self, request: PaymentRequest) -> ProviderPayment:
        """Register the payment and return where to send the customer."""
        ...

    async def fetch(self, provider_payment_id: str) -> ProviderPayment:
        """Read authoritative state from the gateway.

        Used to confirm a webhook rather than trusting its body, and to recover when
        a notification is lost.
        """
        ...

    async def refund(
        self, provider_payment_id: str, amount: Decimal, *, idempotency_key: str
    ) -> ProviderRefund: ...

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        """Authenticate and parse a notification, or raise."""
        ...


_REGISTRY: dict[str, PaymentProvider] = {}


def register(provider: PaymentProvider) -> None:
    _REGISTRY[provider.name.lower()] = provider


def available() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def get(name: str) -> PaymentProvider:
    """Look up a provider, or fail loudly.

    No fallback: an unknown provider is a configuration error. Quietly substituting
    a different one would mean taking money through a gateway nobody chose.
    """
    provider = _REGISTRY.get(name.lower())
    if provider is None:
        raise ConfigurationError(
            "error.payments.unknown_provider", provider=name, known=list(available())
        )
    return provider


def reset_registry() -> None:
    """Test hygiene only."""
    _REGISTRY.clear()
