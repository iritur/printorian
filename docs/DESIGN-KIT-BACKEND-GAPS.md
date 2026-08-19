# What the design kit needs from the backend

> **Superseded in part.** Written when the kit had fourteen screens; it now has
> twenty-one, and the seven newest — post-production, packaging, service,
> purchasing, store, logistics, promo — are not covered here. See
> [DESIGN-KIT-INTEGRATION.md](DESIGN-KIT-INTEGRATION.md) for the full inventory.

Every screen in [`design/`](../design/README.md) checked against the code, and the
work each one implies. Compiled while converting the storefront rather than read
off the mockups, so "missing" here means *grepped for and not found*, not
*not obvious*.

Ordered by what blocks what. Sizes are relative, not estimates.

---

## 1. Nothing needed — these screens have their backend

Listed so nobody builds them twice.

| Screen | Already there |
|---|---|
| `configurator.html` | Quote, per-option delta, mesh analysis, material catalogue, colour slots |
| `checkout.html` | Order placement, pinned price snapshot, payment start, receipt lines |
| `cabinet.html` | Order list, 9-stage pipeline, queue position, predicted start, SLA credit |
| `orders.html` | Desk, status advance, refunds, allowed-transition list |
| `materials.html` | Specs, lots, locations, AMS mapping, derived status tags |
| `fleet.html` | Registry, capabilities, live telemetry, AMS slots, `ServiceOperation` with kind, interval and hours-at-last-service |
| `users.html` | Roles, the permission matrix, user create/activate |
| `index.html` | Component reference, not a product screen |

Two corrections to earlier notes in `DESIGN-KIT-PLAN.md`: the service card is
**not** a single boolean — `ServiceOperation` carries `kind`, `interval_hours` and
`last_done_at_hours`, and `maintenance_due` is derived from them. And the prep
queue now has both its endpoints and its screen.

---

## 2. Small, self-contained

Each is a handful of endpoints against entities that already exist.

### 2.1 Session listing and single revocation — `users.html`
`Session` is a model and deactivating a user revokes all of theirs, but
`_revoke_all` is private and all-or-nothing. Nothing lists sessions or ends one.

- `GET /users/{id}/sessions` → id, user agent, created, last seen, expires
- `DELETE /sessions/{id}` → revoke one, not the set
- The screen shows "SESSIONS :: 6 АКТИВНЫХ" in its chrome, so a count is wanted
  on the users table too

### 2.2 Password recovery — `auth.html`
Sign-in and register exist. Recovery does not — no `otp`, `reset_code` or
`recovery` anywhere in the backend.

- A short-lived code entity: user, six digits, issued/expires, attempt counter
- `POST /auth/recover` (issue, always answering the same way whether or not the
  address is known — a differing answer is an account-existence oracle)
- `POST /auth/recover/verify` → a one-use token
- `POST /auth/recover/reset`
- The kit chose a **code, not a link**, because a code cannot be forwarded by
  accident. That decision belongs in an ADR before it is built.

### 2.3 Per-order margin — `orders.html`
The desk shows margin per order. `OrderView` has no such field, though the pinned
`Breakdown` contains everything needed to compute it.

- Derive from the stored breakdown at read time; do **not** store a second copy
  that can disagree with the snapshot it came from.

---

## 3. Medium — new entities, no new subsystems

### 3.1 The customer's own record — `account.html` — **done**

Built as `contexts/account` plus the `/account` router. What landed:

| Section | Built as |
|---|---|
| Адреса доставки | `Address`, per customer, one default; copied into an order at checkout, never linked |
| Оплата и документы | Receipts and refunds **derived** from settled payments — no documents table. Saved cards deliberately absent, see below |
| Уведомления | Five per-event switches on `notification_prefs`, plus the locked lateness-credit row and the journal subscription |
| Мои модели | `ModelLibrary.uploaded_by`, with a scoped `/account/models/{id}/file` so a customer can re-order their own upload |
| Безопасность | Session listing with device, address and last-seen; end-one and end-all-but-current; password change; export; account closure |

The **tier ladder** is `contexts/pricing/loyalty.py` — thresholds in roubles of
lifetime spend — projected onto one customer by `account/ladder.py`, which is what
supplies the gap the screen leads with. It is also *applied*: every path that
produces a price resolves the caller's tier through `api/routers/_loyalty.py`, so
the «−4%» on the badge is the same four percent the engine takes off.

Two things were **not** built, and both because the alternative would have been a
promise the farm cannot keep:

- **Saved payment methods.** A saved card is a gateway token; no gateway has been
  exercised against the real thing (README), so the entity would be plumbing that
  can never be tested. The panel states the position instead.
- **Two-factor authentication.** No TOTP anywhere in the backend. The row is drawn
  disabled with the reason on it, using the kit's own idiom for a control that
  cannot move.

Mail delivery is still absent — the preferences are stored and honoured by nothing,
because nothing in this system sends email. The panel's footer says so.

### 3.2 The settings store — `settings.html`
15 sections, ~100 parameters, all currently constants in code. The kit's
identifiers are the real ones (`margin_percent`, `weight_material_headroom`,
`guard_tier_cliffs`, `price_variance_tolerance`, `telemetry_poll_seconds`), which
is what makes this schedulable rather than a design exercise.

- Typed settings table: key, value, type, default, section, updated by, updated at
- Reads fall back to the code default, so an empty table behaves exactly as today
- `RateSnapshot`, `SchedulingPolicy` and `core.config` read through it
- **Pricing purity is the constraint** (ADR-0002): resolve the snapshot once at
  the edge and pass it in. A settings read inside the engine makes pricing depend
  on I/O and `import-linter` will fail the build — correctly
- Change audit log, retained for `audit_retention_days`
- Ends the `material_procurement_flat = 500 ₽` placeholder

### 3.3 The journal — `blog.html`, `blog-post.html`
No backend at all. Independent of everything else, which is why it can go last.

- `Post`: slug, title, category, body, contents, publication state, published at
- Public list and detail; author-only draft access
- The kit renders a contents sidebar and reading progress from the body's
  headings — that is a client concern, not a field

---

## 4. Large — new subsystems

### 4.1 The model catalogue — `catalog.html`
`contexts/catalog` is `ModelAsset` and `PreparedPlate`; there is no library.

- Categories, tags, difficulty, ratings, print counts
- Faceted search: OR within a group, AND across groups, over one pass with sort
- Eight sort keys, cost-like ascending and quality-like descending by default
- **Measured** print time and price from the last real print — the page's whole
  claim. Needs completed jobs to draw from, so it is thin until the farm has run
- Until a real print exists the screen must show the estimate **labelled as an
  estimate**. Presenting one as the other is ADR-0007's defect wearing a
  different hat

### 4.2 The dashboard — `dashboard.html`
Fleet state and order counts exist; there is no aggregation layer. Nothing in the
backend answers any of these.

| Panel | Needs |
|---|---|
| KPI tiles | Orders / printers / finance, each with a delta against the previous period — so a period-over-period query, not a snapshot |
| Status wall | Already served by the fleet event stream |
| 12-hour schedule | Per-printer ETA and queue depth on a time axis |
| Filament headroom | Loaded / in stock / **committed to the queue**. The third is the number nobody tracks and the one that causes the stall — it means summing `grams_required` across planned jobs per material |
| Stage funnel | Count per order stage; needs Phase 5's post-production stages to be real |
| Куда ушли деньги | Spend by category over a period — the P&L side of Phase 6 |

### 4.3 Post-production — every screen that counts a stage
`contexts/postproduction` does not exist. The cabinet's nine-stage pipeline, the
dashboard's funnel and the floor stations all count stages that are currently
only names in `OrderStatus`.

This is roadmap Phase 5 and is the largest item here.

---

## 5. Dependency order

```
2.1, 2.2, 2.3   independent — any time
3.1, 3.2, 3.3   independent of each other
4.1  needs completed jobs before its central claim is true
4.3  ──→ 4.2's stage funnel and the cabinet's later stages
```

The only hard ordering is post-production before the parts of the dashboard that
count post-production. Everything else is schedulable in any order.

---

## 6. What this does not include

Front-end-only work — restyling, the nav overlay, the theme switch — is tracked
in [DESIGN-KIT-PLAN.md](DESIGN-KIT-PLAN.md) as Slices B and C. Nothing here is a
styling task; every item is a thing the server cannot currently answer.
