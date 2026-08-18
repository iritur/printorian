"""YooKassa gateway (API v3).

**Written from the documented API shape and NOT yet exercised against a live
merchant account.** That is stated here rather than discovered later: V1 shipped a
printer connector built the same way, which returned plausible data while talking to
an endpoint that did not exist. Until someone runs this against real credentials it
is unverified, and the contract tests below only prove it is self-consistent.

Two properties of this gateway drive the design:

* **Idempotence-Key** is a first-class header. The same key returns the same payment
  instead of creating a second one, which is what makes retrying a create safe.
* **Webhooks are not signed.** YooKassa authenticates them by source IP only, so the
  body can never be trusted on its own. This adapter checks the source address, and
  the service *always* re-reads the payment from the API before settling anything.
"""

from __future__ import annotations

import base64
import json as json_module
from decimal import Decimal
from ipaddress import ip_address, ip_network
from typing import Any

import httpx

from printorian.contexts.payments.policies import PaymentStatus
from printorian.contexts.payments.provider import (
    PaymentProviderError,
    PaymentRequest,
    ProviderPayment,
    ProviderRefund,
    WebhookEvent,
    WebhookVerificationError,
)

API_ROOT = "https://api.yookassa.ru/v3"

#: Anything at or above this is an error response.
HTTP_ERROR_FLOOR = 400

#: Networks YooKassa sends notifications from, per their documentation. Keeping the
#: list here (rather than in config) makes it reviewable; it is overridable because
#: published ranges do change.
DEFAULT_WEBHOOK_NETWORKS: tuple[str, ...] = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "77.75.154.128/25",
    "2a02:5180::/32",
)

#: YooKassa's vocabulary mapped onto ours. `waiting_for_capture` is deliberately
#: PENDING: the money is authorised but not taken, so nothing may ship yet.
_STATUS_MAP: dict[str, PaymentStatus] = {
    "pending": PaymentStatus.PENDING,
    "waiting_for_capture": PaymentStatus.PENDING,
    "succeeded": PaymentStatus.SUCCEEDED,
    "canceled": PaymentStatus.CANCELLED,
}


class YooKassaProvider:
    """Adapter for YooKassa's REST API."""

    def __init__(
        self,
        shop_id: str,
        secret_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        webhook_networks: tuple[str, ...] = DEFAULT_WEBHOOK_NETWORKS,
        timeout: float = 20.0,
    ) -> None:
        if not shop_id or not secret_key:
            raise PaymentProviderError("error.payments.missing_credentials", provider="yookassa")
        self._shop_id = shop_id
        self._secret_key = secret_key
        self._client = client
        self._timeout = timeout
        self._networks = tuple(ip_network(net) for net in webhook_networks)

    @property
    def name(self) -> str:
        return "yookassa"

    # -- HTTP ------------------------------------------------------------

    @property
    def _auth_header(self) -> str:
        raw = f"{self._shop_id}:{self._secret_key}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": self._auth_header, "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotence-Key"] = idempotency_key

        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.request(method, f"{API_ROOT}{path}", json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise PaymentProviderError(
                "error.payments.provider_unreachable", detail=str(exc)
            ) from exc
        finally:
            if self._client is None:
                await client.aclose()

        if response.status_code >= HTTP_ERROR_FLOOR:
            raise PaymentProviderError(
                "error.payments.provider_rejected",
                status=response.status_code,
                body=response.text[:500],
            )
        payload: dict[str, Any] = response.json()
        return payload

    # -- contract --------------------------------------------------------

    async def create(self, request: PaymentRequest) -> ProviderPayment:
        body: dict[str, Any] = {
            "amount": {"value": f"{request.amount:.2f}", "currency": request.currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": request.return_url},
            "description": request.description[:128],
            "metadata": {"order_number": request.order_number, **request.metadata},
        }
        if request.receipt:
            # 54-ФЗ: the fiscal receipt travels with the payment.
            body["receipt"] = {
                "customer": {"email": request.customer_email},
                "items": [
                    {
                        "description": line.description[:128],
                        "quantity": str(line.quantity),
                        "amount": {"value": f"{line.amount:.2f}", "currency": request.currency},
                        "vat_code": line.vat_code,
                    }
                    for line in request.receipt
                ],
            }

        payload = await self._request(
            "POST", "/payments", json=body, idempotency_key=request.idempotency_key
        )
        return self._to_payment(payload)

    async def fetch(self, provider_payment_id: str) -> ProviderPayment:
        payload = await self._request("GET", f"/payments/{provider_payment_id}")
        return self._to_payment(payload)

    async def refund(
        self, provider_payment_id: str, amount: Decimal, *, idempotency_key: str
    ) -> ProviderRefund:
        payload = await self._request(
            "POST",
            "/refunds",
            json={
                "payment_id": provider_payment_id,
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            },
            idempotency_key=idempotency_key,
        )
        return ProviderRefund(
            refund_id=str(payload.get("id", "")),
            provider_payment_id=provider_payment_id,
            amount=Decimal(str(payload.get("amount", {}).get("value", amount))),
            succeeded=payload.get("status") == "succeeded",
            raw=payload,
        )

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        """Check the source address and parse the body.

        This establishes *plausibility*, not truth. YooKassa does not sign
        notifications, so the payment is re-read from the API before anything is
        settled — see ``PaymentsService.handle_webhook``.
        """
        lowered = {key.lower(): value for key, value in headers.items()}
        source = lowered.get("x-forwarded-for", "").split(",")[0].strip() or lowered.get(
            "x-real-ip", ""
        )
        if not source:
            raise WebhookVerificationError("error.payments.webhook_no_source_ip")
        if not self._is_trusted(source):
            raise WebhookVerificationError("error.payments.webhook_untrusted_source", source=source)

        try:
            payload = json_module.loads(body)
            obj = payload["object"]
        except (ValueError, KeyError) as exc:
            raise WebhookVerificationError("error.payments.webhook_malformed") from exc

        return WebhookEvent(
            provider_payment_id=str(obj["id"]),
            status=_STATUS_MAP.get(str(obj.get("status")), PaymentStatus.PENDING),
            amount=Decimal(str(obj.get("amount", {}).get("value", "0"))),
            event_id=str(payload.get("event", "")),
            raw=payload,
        )

    def _is_trusted(self, source: str) -> bool:
        try:
            address = ip_address(source)
        except ValueError:
            return False
        return any(address in network for network in self._networks)

    @staticmethod
    def _to_payment(payload: dict[str, Any]) -> ProviderPayment:
        confirmation = payload.get("confirmation") or {}
        return ProviderPayment(
            provider_payment_id=str(payload["id"]),
            status=_STATUS_MAP.get(str(payload.get("status")), PaymentStatus.PENDING),
            amount=Decimal(str(payload.get("amount", {}).get("value", "0"))),
            currency=str(payload.get("amount", {}).get("currency", "RUB")),
            confirmation_url=confirmation.get("confirmation_url"),
            raw=payload,
        )
