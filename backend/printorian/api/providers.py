"""Resolving the configured payment gateway.

Kept out of ``deps`` so the rule that matters is easy to find: a mock gateway must
be impossible to reach from a production deployment. The mock enforces that itself
(ADR-0007), and this refuses to select it as well — two independent guards, because
the consequence of getting it wrong is orders marked paid without money moving.
"""

from __future__ import annotations

from printorian.contexts.payments import (
    ManualPaymentProvider,
    MockPaymentProvider,
    PaymentProvider,
)
from printorian.contexts.payments.providers.yookassa import YooKassaProvider
from printorian.core.config import Settings
from printorian.core.errors import ConfigurationError


def build_provider(
    name: str, settings: Settings, *, cache: dict[str, PaymentProvider] | None = None
) -> PaymentProvider:
    """Construct a gateway by name, or fail loudly.

    ``cache`` lets the caller keep one instance alive across requests. That matters
    for the mock, which stands in for an external system: a gateway rebuilt per
    request would forget that a customer had paid, and the webhook path could then
    only be tested by bypassing it.
    """
    chosen = (name or settings.payment_provider).lower()

    if chosen == "mock":
        if settings.is_production:
            raise ConfigurationError(
                "error.payments.mock_in_production",
                hint="Set PRINTORIAN_PAYMENT_PROVIDER to a real gateway.",
            )
        if cache is not None:
            existing = cache.get("mock")
            if existing is None:
                existing = MockPaymentProvider(settings=settings)
                cache["mock"] = existing
            return existing
        return MockPaymentProvider(settings=settings)

    if chosen == "manual":
        return ManualPaymentProvider()

    if chosen == "yookassa":
        return YooKassaProvider(
            settings.yookassa_shop_id,
            settings.yookassa_secret_key.get_secret_value(),
        )

    raise ConfigurationError(
        "error.payments.unknown_provider",
        provider=chosen,
        known=["mock", "manual", "yookassa"],
    )
