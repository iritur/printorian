"""Payment gateway adapters, independent of the ordering flow."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import OrderingService, OrderStatus
from printorian.contexts.payments import (
    MockPaymentProvider,
    PaymentsService,
    PaymentStatus,
    StartPayment,
    WebhookVerificationError,
)
from printorian.contexts.payments.providers.manual import ManualPaymentProvider
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import ConfigurationError
from printorian.core.events import EventBus
from tests.unit.test_payments import an_order


@pytest.fixture
def ordering(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> OrderingService:
    return OrderingService(db_session, clock, bus)


# ------------------------------------------------------------ providers


def test_the_mock_gateway_refuses_to_exist_in_production(
    production_settings: Settings,
) -> None:
    """ADR-0007, applied to money: a fake gateway must be unreachable in production."""
    with pytest.raises(ConfigurationError) as excinfo:
        MockPaymentProvider(settings=production_settings)
    assert excinfo.value.code == "error.payments.mock_in_production"


async def test_manual_provider_never_invents_an_upstream_answer() -> None:
    """Returning a plausible "still pending" is exactly the V1 failure mode."""
    from printorian.contexts.payments.provider import PaymentProviderError

    provider = ManualPaymentProvider()
    with pytest.raises(PaymentProviderError):
        await provider.fetch("manual-1")
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook({}, b"{}")


async def test_manual_settlement_shares_the_gateway_settlement_path(
    db_session: AsyncSession, clock: FixedClock, bus: EventBus, ordering: OrderingService
) -> None:
    """Bank transfers and cards must not have two different settlement routines."""
    provider = ManualPaymentProvider()
    service = PaymentsService(db_session, clock, bus, ordering)

    order = await an_order(ordering)
    payment = await service.start(StartPayment(order_id=order.id, provider="manual"), provider)
    assert payment.confirmation_url is None  # nobody is sent anywhere

    event = ManualPaymentProvider.settlement(
        payment.confirmation_url or f"manual-{order.number}", order.total, reference="statement-42"
    )
    # The operator's confirmation carries the order's amount, so it reconciles.
    settled = await service.settle_manually(payment.id, event)

    assert settled.status is PaymentStatus.SUCCEEDED
    assert (await ordering.get(order.id)).status is OrderStatus.PAID


def test_provider_registry_refuses_unknown_gateways(settings: Settings) -> None:
    from printorian.contexts.payments import get, register, reset_registry

    reset_registry()
    register(MockPaymentProvider(settings=settings))
    assert get("mock").name == "mock"

    with pytest.raises(ConfigurationError) as excinfo:
        get("definitely-not-a-gateway")
    assert excinfo.value.code == "error.payments.unknown_provider"
    reset_registry()
