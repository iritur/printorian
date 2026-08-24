"""The YooKassa adapter: the request it builds, checked against the documented API.

These tests prove the HTTP *shape*, not that the gateway accepts it. The demo store
only exercises bank cards and the YooMoney wallet; T-Pay ('tinkoff_bank') needs a
live merchant account (the docs: "Other payment methods can only be tested in a
real store"). So the best a credential-free test can do — and the thing that most
often breaks — is assert that the body we send is exactly what the documentation
specifies, and that a live failure would then be a credential problem rather than a
shape problem.

The transport is an httpx.MockTransport, so no network is touched; every request is
recorded and its body inspected.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import httpx
import pytest

from printorian.contexts.payments.policies import PaymentStatus
from printorian.contexts.payments.provider import (
    PaymentProviderError,
    PaymentRequest,
    ReceiptLine,
    WebhookVerificationError,
)
from printorian.contexts.payments.providers.yookassa import TINKOFF_BANK, YooKassaProvider


def _make_provider(payload: dict):
    """A YooKassaProvider wired to a transport that records every request."""

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YooKassaProvider("123456", "live_secret", client=client)
    return provider, client, seen


def _payment_request(**kwargs) -> PaymentRequest:
    defaults = {
        "order_number": "ORD-42",
        "amount": Decimal("1234.56"),
        "currency": "RUB",
        "description": "Printorian ORD-42",
        "return_url": "https://shop.example/orders",
        "idempotency_key": "key-1",
    }
    defaults.update(kwargs)
    return PaymentRequest(**defaults)


async def test_create_without_a_method_offers_the_gateway_menu() -> None:
    provider, client, seen = _make_provider(
        {
            "id": "p1",
            "status": "pending",
            "amount": {"value": "1234.56", "currency": "RUB"},
            "confirmation": {"type": "redirect", "confirmation_url": "https://pay.example/confirm"},
        }
    )
    try:
        result = await provider.create(_payment_request())
    finally:
        await client.aclose()

    body = seen["body"]
    assert body["amount"] == {"value": "1234.56", "currency": "RUB"}
    assert body["capture"] is True
    assert body["confirmation"] == {"type": "redirect", "return_url": "https://shop.example/orders"}
    assert body["metadata"] == {"order_number": "ORD-42"}
    # No method chosen: the customer picks on the gateway's page, not in our code.
    assert "payment_method_data" not in body
    assert result.confirmation_url == "https://pay.example/confirm"


async def test_t_pay_requests_the_tinkoff_bank_method() -> None:
    provider, client, seen = _make_provider(
        {"id": "p2", "status": "pending", "amount": {"value": "1234.56", "currency": "RUB"}}
    )
    try:
        await provider.create(_payment_request(payment_method_type=TINKOFF_BANK))
    finally:
        await client.aclose()

    assert TINKOFF_BANK == "tinkoff_bank"
    assert seen["body"]["payment_method_data"] == {"type": "tinkoff_bank"}


async def test_the_fiscal_receipt_travels_with_the_payment() -> None:
    provider, client, seen = _make_provider(
        {"id": "p3", "status": "pending", "amount": {"value": "1234.56", "currency": "RUB"}}
    )
    try:
        await provider.create(
            _payment_request(
                customer_email="buyer@example.com",
                receipt=(
                    ReceiptLine(
                        description="3D printing, order ORD-42",
                        quantity=Decimal(1),
                        amount=Decimal("1234.56"),
                        vat_code=1,
                    ),
                ),
            )
        )
    finally:
        await client.aclose()

    receipt = seen["body"]["receipt"]
    assert receipt["customer"] == {"email": "buyer@example.com"}
    assert receipt["items"][0]["description"] == "3D printing, order ORD-42"
    assert receipt["items"][0]["vat_code"] == 1


async def test_create_authenticates_and_is_idempotent() -> None:
    provider, client, seen = _make_provider(
        {"id": "p4", "status": "pending", "amount": {"value": "1234.56", "currency": "RUB"}}
    )
    try:
        await provider.create(_payment_request())
    finally:
        await client.aclose()

    expected = "Basic " + base64.b64encode(b"123456:live_secret").decode()
    assert seen["headers"]["authorization"] == expected
    assert seen["headers"]["idempotence-key"] == "key-1"


async def test_status_vocabulary_maps_onto_ours() -> None:
    mapping = {
        "pending": PaymentStatus.PENDING,
        "waiting_for_capture": PaymentStatus.PENDING,
        "succeeded": PaymentStatus.SUCCEEDED,
        "canceled": PaymentStatus.CANCELLED,
    }
    for upstream, ours in mapping.items():
        payment = YooKassaProvider._to_payment(
            {"id": "p", "status": upstream, "amount": {"value": "10.00", "currency": "RUB"}}
        )
        assert payment.status is ours


async def test_fetch_reads_authoritative_state() -> None:
    provider, client, seen = _make_provider(
        {"id": "p5", "status": "succeeded", "amount": {"value": "10.00", "currency": "RUB"}}
    )
    try:
        result = await provider.fetch("p5")
    finally:
        await client.aclose()

    assert seen["method"] == "GET"
    assert seen["url"].endswith("/v3/payments/p5")
    assert result.status is PaymentStatus.SUCCEEDED
    assert result.amount == Decimal("10.00")


async def test_refund_posts_the_right_body() -> None:
    provider, client, seen = _make_provider(
        {"id": "r1", "status": "succeeded", "amount": {"value": "50.00", "currency": "RUB"}}
    )
    try:
        result = await provider.refund("p5", Decimal("50.00"), idempotency_key="ref-key")
    finally:
        await client.aclose()

    assert seen["url"].endswith("/v3/refunds")
    assert seen["body"] == {"payment_id": "p5", "amount": {"value": "50.00", "currency": "RUB"}}
    assert result.succeeded is True


async def test_an_error_response_is_not_silently_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"type": "error", "description": "bad request"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YooKassaProvider("123456", "live_secret", client=client)
    try:
        with pytest.raises(PaymentProviderError):
            await provider.create(_payment_request())
    finally:
        await client.aclose()


async def test_webhook_accepts_only_the_documented_source_networks() -> None:
    provider = YooKassaProvider("123456", "live_secret")
    body = json.dumps(
        {
            "event": "payment.succeeded",
            "object": {
                "id": "p9",
                "status": "succeeded",
                "amount": {"value": "10.00", "currency": "RUB"},
            },
        }
    ).encode()

    event = provider.verify_webhook({"x-forwarded-for": "185.71.76.1"}, body)
    assert event.provider_payment_id == "p9"
    assert event.status is PaymentStatus.SUCCEEDED
    assert event.amount == Decimal("10.00")

    # A proxy chain uses the left-most, client-facing address.
    event = provider.verify_webhook({"x-forwarded-for": "185.71.76.1, 10.0.0.2"}, body)
    assert event.provider_payment_id == "p9"


async def test_webhook_rejects_untrusted_or_missing_sources() -> None:
    provider = YooKassaProvider("123456", "live_secret")
    body = json.dumps(
        {"event": "payment.succeeded", "object": {"id": "p9", "status": "succeeded"}}
    ).encode()

    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook({"x-forwarded-for": "203.0.113.7"}, body)
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook({}, body)


async def test_missing_credentials_are_refused_at_construction() -> None:
    with pytest.raises(PaymentProviderError):
        YooKassaProvider("", "")
