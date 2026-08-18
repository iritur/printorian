"""Payments: start a checkout, receive gateway notifications, issue refunds.

The webhook endpoint is the one place in the API that is deliberately
unauthenticated — gateways cannot present a session. Its safety comes from the
provider adapter verifying the notification and the service re-reading the payment
from the gateway before believing anything.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from printorian.api.deps import AppSettings, CurrentActor, Payments, requires
from printorian.api.providers import build_provider
from printorian.contexts.identity import Permission
from printorian.contexts.payments import (
    PaymentProvider,
    PaymentView,
    RefundRequest,
    StartPayment,
)
from printorian.contexts.payments.providers.manual import ManualPaymentProvider
from printorian.core.ids import EntityId

router = APIRouter(prefix="/payments", tags=["payments"])


def gateway(request: Request, name: str, settings: AppSettings) -> PaymentProvider:
    """Resolve a gateway, keeping one instance per app.

    The cache lives on ``app.state`` so a stand-in gateway behaves like the external
    system it replaces and survives between requests. Without it the mock would
    forget that a customer had paid, and the webhook path could only be tested by
    bypassing it.
    """
    cache: dict[str, PaymentProvider] | None = getattr(request.app.state, "payment_gateways", None)
    if cache is None:
        cache = {}
        request.app.state.payment_gateways = cache
    return build_provider(name, settings, cache=cache)


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_payment(
    data: StartPayment,
    request: Request,
    actor: CurrentActor,
    payments: Payments,
    settings: AppSettings,
) -> PaymentView:
    """Begin collecting for an order and return where to pay.

    There is no amount in the request. It is read from the order.
    """
    provider = gateway(request, data.provider or settings.payment_provider, settings)
    return await payments.start(data, provider)


@router.post("/webhook/{provider_name}", status_code=status.HTTP_200_OK)
async def gateway_webhook(
    provider_name: str, request: Request, payments: Payments, settings: AppSettings
) -> dict[str, str]:
    """Receive a gateway notification.

    Always answers 200 once the notification is verified, including for duplicates:
    a non-2xx tells the provider to retry, and retrying a message we have already
    processed correctly achieves nothing but load. Verification failures still
    raise, because those are not our duplicates — they are someone else's forgery.
    """
    provider = gateway(request, provider_name, settings)
    body = await request.body()
    headers = dict(request.headers)

    result = await payments.handle_webhook(provider, headers, body)
    return {"status": "processed" if result is not None else "duplicate"}


@router.get("/order/{order_id}")
async def payments_for_order(
    order_id: EntityId, actor: CurrentActor, payments: Payments
) -> list[PaymentView]:
    """Payment attempts against one order, for the cabinet and for support."""
    return await payments.for_order(order_id)


@router.get("/{payment_id}")
async def get_payment(payment_id: EntityId, actor: CurrentActor, payments: Payments) -> PaymentView:
    return await payments.get(payment_id)


@router.post(
    "/{payment_id}/refund",
    dependencies=[Depends(requires(Permission.ISSUE_REFUND))],
)
async def refund_payment(
    payment_id: EntityId,
    data: RefundRequest,
    request: Request,
    payments: Payments,
    settings: AppSettings,
    provider_name: str = "",
) -> PaymentView:
    """Return money, in whole or in part. Owner-only."""
    provider = gateway(request, provider_name, settings)
    return await payments.refund(payment_id, provider, amount=data.amount, reason=data.reason)


@router.post(
    "/{payment_id}/refund-sla-credit",
    dependencies=[Depends(requires(Permission.ISSUE_REFUND))],
)
async def refund_sla_credit(
    payment_id: EntityId,
    request: Request,
    payments: Payments,
    settings: AppSettings,
    provider_name: str = "",
) -> PaymentView:
    """Pay back exactly what lateness owes — the scenario's late-delivery discount,
    for a customer who has already been charged."""
    provider = gateway(request, provider_name, settings)
    return await payments.refund_sla_credit(payment_id, provider)


@router.get("/providers/available")
async def available_providers(
    settings: AppSettings,
    _: Annotated[object, Depends(requires(Permission.VIEW_FINANCIALS))] = None,
) -> dict[str, object]:
    """Which gateways this deployment can use, and which is the default."""
    usable = ["manual", "yookassa"] if settings.is_production else ["mock", "manual", "yookassa"]
    return {"configured": settings.payment_provider, "available": usable}


@router.post("/{payment_id}/settle-manually", status_code=status.HTTP_200_OK)
async def settle_manually(
    payment_id: EntityId,
    actor: CurrentActor,
    payments: Payments,
    reference: str = "",
    _: Annotated[object, Depends(requires(Permission.VIEW_FINANCIALS))] = None,
) -> PaymentView:
    """Record that a bank transfer or cash payment arrived.

    Takes the same settlement path as a gateway notification, and is attributed to
    the operator who confirmed it — money moving on someone's say-so should carry
    their name.
    """
    payment = await payments.get(payment_id)
    event = ManualPaymentProvider.settlement(
        f"manual-{payment.id}", payment.amount, reference=reference
    )
    return await payments.settle_manually(payment_id, event, actor_id=actor.user_id)
