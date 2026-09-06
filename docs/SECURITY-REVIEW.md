# Security review — payments, uploads, and the trust boundary

A read of the money path and the two places an unauthenticated request reaches real
work: the payments routes, the gateway webhook, and the anonymous quote upload.
Findings are ranked by consequence, and each carries either the name of the test
that proves it fixed or the decision it is waiting on. **No status here is a
claim** — check the file, and check the named test.

Every number below was measured on this branch, or is labelled as not measured
(ADR-0007 / [CLAUDE.md](../CLAUDE.md) §1). Line numbers are as of the commit that
added this file.

---

## 1. What was reviewed, and what was not

**Reviewed.** `api/routers/payments.py`, `api/routers/orders.py`,
`contexts/payments/` (service, policies, and the three provider adapters),
`api/deps.py`'s actor and throttle resolution, `api/middleware.py`'s body limit,
`core/storage.py`'s path construction, `api/routers/auth.py`'s lockout, and the
anonymous entry points — `POST /pricing/quote`, `POST /payments/webhook/{provider}`
and `/health/*`.

**Not reviewed, and why.**

* **The edge.** `find deploy -type f` returns thirteen files and none of them
  configures a tunnel or a VPS. The four mentions in that tree —
  `compose.prod.yml:242`, `readiness-check.sh:344`, `storefront.Caddyfile:4` and
  `:25` — all say the same thing: it does not exist yet.
  [INFRASTRUCTURE.md](INFRASTRUCTURE.md) Stage 3 is unbuilt. There is nothing to
  review, and writing a section about a WireGuard configuration nobody has written
  would be inventing the thing this repository's first rule forbids.
* **Dependency and container supply chain.** Covered by the release gate in
  [INFRASTRUCTURE.md](INFRASTRUCTURE.md), not re-read here.
* **The frontend.** The console's content policy is argued in
  `deploy/console.Caddyfile`; this review is about the API.

One thing Stage 3 already owes, recorded here so it is not rediscovered:
`api/routers/health.py:17-21` says the storefront's edge must not forward
`/health/*`. Today `deploy/storefront.Caddyfile` forwards all of `/api/*`, so
`/api/health/ready` is reachable through the rehearsal edge. That is **not** a
finding: that Caddy is bound to the LAN, which is the same reason the whole file
exists as rehearsal rather than as the real thing.

---

## 2. Findings

### F1 — Any signed-in account could read and start another customer's payments

*Fixed on this branch.*

`api/routers/payments.py`, four routes: `POST /payments`,
`GET /payments/order/{order_id}`, `GET /payments/{payment_id}`, and — through the
first of those — the order's status. Each took an id and answered. The payments
context knows an `order_id` and nothing about who placed the order, so no ownership
check existed anywhere on the path; `orders.py` had the rule and payments had never
been given it.

**Impact.** With any customer account: read another customer's amounts, currency,
refunded totals and refund history, by order id or by payment id; and start a
payment against somebody else's order, which as a side effect advanced that order
from `draft` to `awaiting_payment`.

**Fixed by** `api/routers/_order_access.py`, one helper, called from both payments
and orders so there is one definition rather than a fourth hand-written copy.

Proved by, in `backend/tests/api/test_payments_access.py`:
`test_a_customer_cannot_list_another_customers_payments`,
`test_a_customer_cannot_read_another_customers_payment`,
`test_a_customer_cannot_start_a_payment_on_another_customers_order` (which also
asserts the order still reads `draft`, because a refusal that kept the side effect
is not a fix), and `test_a_customer_reads_their_own_payments` — so the guard cannot
pass by refusing everybody.

The staff half of the rule is `VIEW_FINANCIALS`, not `VIEW_ALL_ORDERS`. A
`PaymentView` is rubles end to end and [CLAUDE.md](../CLAUDE.md) §1 keeps the money
permission apart from the production ones. This changes no screen today —
`contexts/identity/policies.py:109-116` gives MANAGER both, and OWNER holds
everything — and `test_staff_reading_payments_need_view_financials` exists so that
the day they are split apart a test fails instead of a customer's amounts appearing
on a production screen.

One §1 consequence fell out of the same change and is asserted deliberately rather
than left as an accident: `GET /payments/order/{unknown}` used to answer `[]`, which
reads as "this order took no payments". It is now a 404
(`test_an_unknown_order_id_is_a_404_not_an_empty_list`).

### F2 — A refund could be sent through a gateway that never held the money

*Fixed on this branch.*

`api/routers/payments.py`, both refund routes, which took `provider_name` as a
query parameter defaulting to the deployment's configured gateway. So
`POST /payments/{id}/refund?provider_name=manual` against a card payment resolved
`ManualPaymentProvider`, whose `refund` always reports success — correctly, because
its contract is that a person will send the money afterwards
(`contexts/payments/providers/manual.py:61-71`; that file is right and was left
alone, the defect was routing a card payment into it).

**Impact.** The payment reads `REFUNDED`, `refunded_amount` moves, a refund note is
issued to the customer through `contexts/payments/documents.py`, and the gateway
actually holding the money was never told. Nothing raises. The database and the
money disagree, and the database is the copy. This is the irreversible path, which
is the one [CLAUDE.md](../CLAUDE.md) §2's corollary says most needs a test.

**Fixed twice, on purpose** — the same two-guard shape `api/providers.py:1-7` states
for keeping the mock gateway out of production, and for the same reason:

1. The parameter is gone. Both routes read the payment and resolve the gateway from
   `payment.provider`. That removes the cause rather than validating the input
   ([CLAUDE.md](../CLAUDE.md) §5) — a caller has no business naming the gateway.
2. `PaymentsService.refund` refuses a `provider` whose `.name` differs from
   `payment.provider`, with `error.payments.provider_mismatch` carrying
   `expected`/`actual` as structured details (ADR-0012, never a sentence). That one
   holds for anything calling the service directly, including a worker.

Proved by `backend/tests/unit/test_payment_provider_binding.py`:
`test_a_refund_through_a_different_gateway_is_refused` (asserting on what was *not*
written — no `Refund` row, status and `refunded_amount` unchanged — because an
unguarded mismatch raises nothing to catch),
`test_an_sla_credit_refund_uses_the_gateway_that_took_the_money` (it inherits the
guard by delegating, and the delegation is what a later refactor would quietly
undo), and `test_the_gateway_that_took_the_money_may_still_refund_it`. At the HTTP
level, `test_payments_access.py::test_the_refund_route_ignores_a_caller_supplied_gateway`
asserts against the mock gateway's *own* ledger rather than against the row, since
the row is exactly what the bug would have written.

No i18n key was added. No other `error.payments.*` code has one in
`packages/ui/src/i18n/messages.ts`; adding one means adding it to RU and EN
together, or the typed catalogue fails.

### F3 — The webhook's source check depends on a proxy behaviour nothing states

*Not fixed. Needs the owner's decision — see §5(a).*

`contexts/payments/providers/yookassa.py:205` trusts the **first** entry of
`X-Forwarded-For`; `api/deps.py:throttle_key` trusts the **last**. That looked like
a straightforward inconsistency to resolve in favour of the last hop, and the
resolution was checked against Caddy's documentation before being written:

> "It sets or augments the `X-Forwarded-For` header field." … "For these
> `X-Forwarded-*` headers, by default, the proxy will ignore their values from
> incoming requests, to prevent spoofing."
> — [Caddy, `reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

Caddy appends only where `trusted_proxies` says the caller is itself a proxy, and
**neither Caddyfile in `deploy/` sets `trusted_proxies`**. So the header arriving at
the API has exactly one entry, written by Caddy, and first hop and last hop are the
same address. `deps.py`'s own comment said the opposite ("appends rather than
replaces, which is what `deploy/console.Caddyfile` does"); that sentence is
corrected on this branch, and both Caddyfiles now carry a note saying the absence of
`trusted_proxies` is load-bearing in two places that read opposite ends of the same
header.

**The change was therefore not made**, and this is the reasoning rather than a
preference. In the topology that exists the two reads are identical. In the only
case where they differ — the API reached without the proxy — last-hop is the
*weaker* of the two: a forged `X-Forwarded-For: 10.0.0.9, 185.71.76.1` would then be
accepted, where the first-hop read refuses it. Making the change would trade one
topology's safety for another's on the strength of a topology nobody has chosen.
`tests/unit/test_yookassa_provider.py:217` is left asserting what the code does, for
the same reason: inverting an assertion somebody wrote deliberately, to pin
behaviour this branch is not shipping, is how a fix reads as a regression.

**What actually protects the webhook today** is Caddy's default stripping, plus the
fact that the source check "establishes *plausibility*, not truth" — the payment is
re-read from the gateway before anything settles
(`contexts/payments/service.py:157-162`). Both are real. Neither is written down as
a deployment requirement, and that is the decision in §5(a).

### F4 — A manager can create money but cannot return it

*Not fixed. Needs the owner's decision — see §5(b).*

`POST /payments/{id}/settle-manually` (`api/routers/payments.py:181-199`) is gated on
`VIEW_FINANCIALS`, which MANAGER holds (`policies.py:109-116`). It reaches
`PaymentsService.settle_manually` → `_settle`, which marks the payment `SUCCEEDED`
and advances the order to `PAID`. Both refund routes are gated on `ISSUE_REFUND`,
which appears in no role set but `_OWNER = frozenset(Permission)`.

**Impact.** A manager can record that money arrived when it did not — the mirror of
a refund, one order at a time, with no owner in the loop — while the reversal is
owner-only. The asymmetry may well be intended (the settlement is attributed to the
operator who confirmed it, which a refund is not), but it is not written down
anywhere, and a permission split that nobody has stated is one nobody is
maintaining. This is a design question, so it is asked as one rather than answered.

### F5 — The anonymous quote upload's cost ceiling is 200 MiB

*Not fixed. Needs the owner's decision — see §5(c).*

`POST /pricing/quote` (`api/routers/pricing.py:36-45`) takes `OptionalActor`, so it
is reachable without signing in. The body ceiling is `max_upload_bytes`, default
**200 MiB** (`core/config.py:157`). `contexts/catalog/mesh.py:177` runs
`_VERTEX.findall(data)` over the whole body before any triangle count exists.

Measured on this branch, `tracemalloc` peak *above* the input, for
`mesh._parse_ascii`, at three input sizes to show the ratio is flat rather than
extrapolated from one point:

| ASCII STL line style | 5 MiB | 10 MiB | 20 MiB | ratio | linear at 200 MiB |
|---|---|---|---|---|---|
| a realistic slicer line (49 B) | 26.0 MiB | 51.8 MiB | 103.4 MiB | **5.17–5.19×** | ≈ 1 034 MiB |
| the tightest the regex accepts (13 B) | 49.2 MiB | 98.6 MiB | 197.4 MiB | **9.83–9.87×** | ≈ 1 970 MiB |

The 200 MiB column is a linear extrapolation of a measured, flat ratio — **not
itself measured**. `cpu_workers` defaults to 4 (`core/config.py:74`), so four such
parses may be in flight, and `quote_rate_per_minute` defaults to 30 per address.

**`mesh.py` was deliberately not patched.** The obvious fix — moving the
5 000 000-triangle cap at `mesh.py:33` above the `findall` — cannot work, and the
measurement is what shows it rather than an opinion: the count is `len(values) // 3`,
so it does not exist until `values` has been allocated. And with realistic line
widths a 200 MiB body is only **0.27** of that cap, so the guard would not fire even
if it could be moved. Reordering it would be an ignore wearing a fix's clothes
([CLAUDE.md](../CLAUDE.md) §5). The ceiling is the question, and the ceiling is a
person's decision.

---

## 3. Defences confirmed sound

A review that only finds fault is not falsifiable in both directions. These were
read and are doing what they claim:

* **Path traversal is structurally impossible**, not filtered.
  `core/storage.py:70-80` is the single place an untrusted string reaches the
  filesystem layout, and it checks length and a hex alphabet rather than looking for
  `..`; `:155-166` then builds the path from that validated digest by fan-out. There
  is no call site that can route around it, because the digest *is* the name.
* **An over-sized body is refused at the ASGI layer.**
  `api/middleware.py:130-231` answers a declared `Content-Length` over the ceiling
  before the application is called, and cuts off a chunked body as it streams. The
  docstring is unusually careful about why the chunked path surfaces as 400 rather
  than 413, and it is right: FastAPI owns the status once body parsing has started.
  This is what makes `config.py:156`'s "refused before it is read into memory"
  true — it used to be false.
* **Two independent guards keep the mock gateway out of production.**
  `api/providers.py:33-45` refuses to select it and
  `contexts/payments/providers/mock.py:47-52` refuses to construct itself. Either
  alone would do; both is the correct amount for "orders marked paid without money
  moving".
* **The sign-in lockout is keyed on the pair**, `auth.py:47-58`. Per-account would
  let anyone lock a customer out of their own shop; per-address would let one office
  lock out its colleagues. It also uses `throttle_key` rather than `client_ip`
  (`auth.py:78-82`), which is the difference between a lockout and a suggestion.
* **A webhook is a hint, not a fact.** `contexts/payments/service.py:138-162`
  verifies, records for idempotency, then re-reads the payment from the gateway
  before believing any amount. YooKassa does not sign notifications at all, and this
  is what makes that survivable.
* **The rate ceiling is keyed on the last hop**, `deps.py:116-157`, and
  `tests/api/test_guards_api.py:284-315` proves a forged first entry buys no extra
  allowance.

---

## 4. Accepted behaviour, recorded once

**A 403 and a 404 are distinguishable, so order ids can be probed for existence.**
`GET /orders/{id}` and now the payments routes both load the order first, so an
unknown id is a 404 and someone else's is a 403. The alternative — 404 for both —
loses the ability to tell a customer "that order is not yours" apart from "that
order is gone", and the ids are UUIDv7, so enumeration means guessing 74 random bits
per attempt against a rate-limited endpoint. The refusal *message* is already
identical to a plain permission failure, deliberately
(`api/routers/_order_access.py`), so nothing beyond existence leaks. Recorded as one
accepted behaviour for both routers rather than as two findings.

---

## 5. Decisions this review will not make

Each of these is a trade-off with a real cost on both sides, which makes it the
owner's to make rather than a reviewer's to close. **These are not filed as issues
yet** — the agent that wrote this review does not open issues on the owner's
account. File them, then replace each heading here with its issue number.

**(a) `type:security`, `needs-person` — state the proxy contract, or stop depending
on it.** F3. The webhook's source check and the rate ceiling read opposite ends of
`X-Forwarded-For` and are both correct only while Caddy strips the incoming header.
Options: set `trusted_proxies` explicitly and pick a hop rule for the resulting
chain; or add a readiness assertion that the API is not reachable except through the
proxy; or accept it and record why. Whichever is chosen, the Stage 3 edge inherits
it, and the prose notes now in both Caddyfiles are a warning, not a guarantee.

**(b) `type:security`, `needs-person` — is the settle/refund permission split
intended?** F4. `VIEW_FINANCIALS` creates money and `ISSUE_REFUND` returns it, and
MANAGER holds only the first.

**(c) `type:task`, `needs-person` — the anonymous cost ceiling.** F5, with the
measured ratios above. A separate, smaller `max_upload_bytes` for the anonymous path
is one answer; requiring an actor for uploads over some size is another; leaving it
at 200 MiB because the farm is behind a LAN today is a third and is a legitimate
answer as long as it is a chosen one.

---

**How to check this document.** Every "fixed" above names a test file and a test
name. Run those; if one is missing or passes without its fix, this document is the
thing that is wrong. Every number in §2 F5 was produced by `tracemalloc` around
`mesh._parse_ascii` at 5, 10 and 20 MiB, and the 200 MiB column says on its face
that it is extrapolated.
