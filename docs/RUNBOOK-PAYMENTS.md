# Runbook — payments: YooKassa and T-Pay

The rule this document exists to enforce is the same one that governs the printer
drivers: **a payment gateway is not integrated until a real payment has been run
against it.** `contexts/payments` has three providers — `manual`, `mock` and
`yookassa` — and only the first two have ever moved money in a run.

**What has never happened:** the `YooKassaProvider` has not been exercised against
the gateway with live merchant credentials. It is written from the documented API
shape, and the unit tests in `tests/unit/test_yookassa_provider.py` prove the request
it builds matches that shape — but a live run is the only thing that proves the
endpoint, the credentials and the fiscalisation work together.

---

## 1. The payment method a customer chooses

YooKassa accepts payments through several methods. T-Pay (T-Bank's express checkout)
is one of them; in the API it is the method code `tinkoff_bank`, with **redirect**
confirmation: the customer is sent to a YooMoney page showing a QR code or button
that opens the T-Bank app.

| Property | Value |
|---|---|
| Method type in the API | `tinkoff_bank` |
| Confirmation | redirect (`confirmation_url`) |
| Payment term | 1 hour |
| Holding / capture | up to 7 days, full or partial |
| Refunds | full and partial |
| Limits | 1 ₽ … 700 000 ₽ (raisable) |

How the choice travels through the code:

```
StartPayment.payment_method        →  PaymentsService.start
                                   →  PaymentRequest.payment_method_type
                                   →  body["payment_method_data"] = {"type": ...}
```

An empty `payment_method` means "whatever the gateway offers" — the customer picks
on the payment page and no `payment_method_data` is sent. An unknown method code is
passed through and refused by the gateway, never re-mapped to a different method: a
fallback would charge the customer a way they did not choose.

---

## 2. Testing — what the demo store can and cannot do

YooKassa's own testing page is explicit: **you can test payments via bank cards and
the YooMoney wallet; every other method — T-Pay included — can only be tested in a
real (live) store.** That is a gateway constraint, not a codebase one, and it splits
the proof into two levels:

- **Demo store** — proves the whole integration (create → redirect → webhook →
  settle → refund) end to end with a test card. This is the only way to exercise
  the provider without going live, and it is the thing Phase 2's exit criterion
  asks for.
- **Live store** — the only place T-Pay itself can be completed, because completing
  it means scanning a QR in a real T-Bank app or paying from a real T-Bank account.

### Demo store, step by step

1. Create a demo store in the YooMoney Merchant Profile ("testing payments"). It
   issues a test shop id and secret key.
2. Configure the farm:

```bash
PRINTORIAN_PAYMENT_PROVIDER=yookassa
PRINTORIAN_YOOKASSA_SHOP_ID=<test shop id>
PRINTORIAN_YOOKASSA_SECRET_KEY=<test secret key>
```

3. Start a checkout and pay with a test card on the YooMoney page:

| Card | Meaning |
|---|---|
| `5555 5555 5555 4444` | success (Mastercard, no 3-D Secure) |
| `5555 5555 5555 4477` | success, 3-D Secure required |
| `4111 1111 1111 1111` | success (Visa) |
| `2200 0000 0000 0004` | success (Mir) |
| `4000 0000 0000 0002` | declined |

The full decline-code matrix (and the cards that trigger each `cancellation_details`
reason) lives on YooKassa's testing page and is not reproduced here — it changes,
and this document should not drift from it.

4. Confirm the webhook settled the order, then issue a refund and confirm the
   money-movement record (`payments`, `refunds`, `payment_notifications`) matches
   what the gateway's dashboard says.

### T-Pay, step by step (live store only)

1. Have a live YooKassa merchant account with T-Pay enabled for the shop.
2. Same three environment variables, with live credentials.
3. Checkout sends `payment_method: "tinkoff_bank"`; the returned
   `confirmation_url` shows the T-Bank QR or button.
4. Complete the payment in the real T-Bank app, then verify settlement and refund
   as above.

---

## 3. Webhooks are not signed

YooKassa authenticates notifications by **source IP only** — there is no signature.
`YooKassaProvider.verify_webhook` checks the source against the published ranges
(`DEFAULT_WEBHOOK_NETWORKS`), and `PaymentsService.handle_webhook` **re-reads the
payment from the gateway before believing it**. A webhook body that says "succeeded"
is worth nothing on its own; the fetch is what settles the order. Never loosen the
IP check to make a test pass — an unauthenticated webhook that marks orders paid is
free prints.

**Which address is "the source".** The adapter does not read `X-Forwarded-For`
itself. The API resolves one address per request (`api/client_address.py`): the
last entry of the header when the request came through a proxy named in
`PRINTORIAN_TRUSTED_PROXIES` — on the farm, the two Caddy containers by their fixed
addresses — and the socket peer otherwise. So a notification forwarded by Caddy is
judged by the address Caddy saw, and one that reached the API any other way is
judged by its own address whatever header it carries (security review §5(a)). If
every YooKassa notification starts being refused with
`error.payments.webhook_untrusted_source` naming a `172.29.70.x` address, the proxy's
address and the trusted list have parted company; `test_trusted_proxies_deploy.py`
exists so that cannot merge.

## 3a. Money for an order that was not waiting for it

A customer can pay on a confirmation page after the farm cancelled the order, or a
second attempt can settle on an order another one already paid. The gateway took
the money either way, so the settlement is recorded — and **held** (security review
§5(d)): the order does not move, a `payment_holds` row is written, the payment
appears under «Оплаты на удержании» above the order list, and
`printorian_payments_held` raises the `PaymentHeld` digest.

Nothing automatic happens to the money. Somebody holding `issue_refund` decides:

* **Refund it** from the order's own desk, as any refund. Once the whole amount is
  back, the payment leaves the held list by itself.
* **Keep it** — the customer re-ordered by phone, or asked to apply it elsewhere —
  with «Оставить деньги» and a written reason. The reason and who gave it are kept
  on the hold (`POST /payments/held/{id}/release`).

A manager sees the list (`view_financials`) and can do neither, by design (§5(b)).

---

## 4. Config surface

| Variable | Default | Meaning |
|---|---|---|
| `PRINTORIAN_PAYMENT_PROVIDER` | `mock` | `manual` / `mock` / `yookassa` |
| `PRINTORIAN_YOOKASSA_SHOP_ID` | `""` | merchant shop id |
| `PRINTORIAN_YOOKASSA_SECRET_KEY` | `""` | secret key (a `SecretStr`) |
| `PRINTORIAN_PAYMENT_RETURN_URL` | dev storefront | where the gateway returns the customer |
| `PRINTORIAN_TRUSTED_PROXIES` | loopback | the proxies whose `X-Forwarded-For` is believed (§3) |

`mock` refuses to load in production (ADR-0007 applied to money), and the API
resolves a gateway per request through `api.providers.build_provider`, so the choice
is a deployment setting rather than something the storefront controls.

---

## 5. What remains unproven

- T-Pay has not been completed against a live T-Bank account.
- The demo-store card flow has not been run against a live merchant account either;
  it is the cheaper first proof and should be done before T-Pay.
- 54-ФЗ receipt handling is built and unit-tested but not confirmed against the
  fiscal operator.
