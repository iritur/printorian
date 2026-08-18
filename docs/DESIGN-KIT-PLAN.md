# Realising the design kit

> **Superseded in part.** The kit has grown from fourteen screens to twenty-one.
> The current screen-by-screen inventory and integration plan is
> [DESIGN-KIT-INTEGRATION.md](DESIGN-KIT-INTEGRATION.md). This document remains
> accurate for Slices A and B, which are done.

The kit in [`design/`](../design/README.md) is fourteen static screens in a visual
language ("Harvester") that neither running app speaks yet. This is the plan for
making the system look and behave like it.

Those fourteen screens now land in **two** apps (ADR-0016): the customer-facing
ones in the storefront, the staff ones in the console. The kit's own navigation
already draws that line — `dashboard`, `fleet`, `materials`, `orders`, `users` and
`settings` are staff screens, and the rest are not.

It is written against what the code actually does today, not against the roadmap's
intentions. Where a screen needs something that does not exist, that is said
plainly rather than folded into a line item.

The **backend** side of that — every endpoint, entity and field the kit implies
and the server cannot currently answer — is itemised separately in
[DESIGN-KIT-BACKEND-GAPS.md](DESIGN-KIT-BACKEND-GAPS.md), compiled by grepping
for each one rather than reading it off the mockups.

---

## 1. Where the phases stand

| Phase | State | What is missing |
|---|---|---|
| 0 — Foundations | **Done** | — |
| 1 — Pricing and catalog | **Done** | — |
| 2 — Storefront and payment | **Done except one proof** | YooKassa never exercised against test-mode merchant credentials |
| 3 — Fleet, drivers, console | **Substantially done** | Console rebuilt as a web app (ADR-0016); LAN discovery and the model library still unbuilt, now server-side; telemetry retention/rollups not in the worker; no run against real hardware |
| 4 — Prep queue and scheduler | **Exit criterion met** | Never run against real hardware; the proof below used the `mock` driver |
| 5 — Post-production | **Not started** | `contexts/postproduction` does not exist |
| 6 — Management depth | **Not started** | No procurement, no analytics, no P&L; maintenance is a boolean |
| 7 — Hardening | **Not started** | — |

### Phase 4's exit criterion, demonstrated

> a paid order for a previously-prepared configuration goes from payment to a
> printer starting the job **with no human action**, and the assignment record
> explains which printers were considered and why this one won.

Model and plate storage closed the middle of the chain; hosting the scheduler in
the worker process closed the end of it. A job now walks itself:

```
0. pending      job.created
1. ready        plate.attached
2. assigned     scheduler.assigned
3. dispatching  dispatch.started
4. printing     dispatch.started_printing
```

with an `AssignmentRecord` naming all seven machines considered and the grounds
for each rejection (`reject.busy`, `reject.material_not_loaded`,
`reject.colour_not_loaded`).

**The proof used the `mock` driver.** Nothing here has driven real hardware, and
the Bambu path — MQTT connect, FTPS upload, start — remains the largest unproven
piece in the system.

---

## 2. What the kit asks for

Fourteen screens against the current backend. "Wiring" means the data exists and
the work is frontend; "new backend" means it does not.

| Screen | Backend today | Work |
|---|---|---|
| `configurator.html` | Complete | Restyle. Adds a usage-scenario dialog and a resize control the current page lacks |
| `checkout.html` | Complete | Restyle |
| `cabinet.html` | Complete — 9-stage pipeline, queue position, SLA credit all exist | Restyle |
| `orders.html` | Complete — desk, status advance, refunds | Restyle; per-order margin is not exposed |
| `materials.html` | Complete — lots, locations, pricing | Restyle |
| `fleet.html` | Mostly — registry, AMS slots, telemetry | Service card needs real maintenance records; today `maintenance_due` is one boolean |
| `auth.html` | Sign-in and register exist | **Recovery by six-digit code does not exist** |
| `users.html` | Roles and the permission matrix exist; `Session` is a model, and deactivating a user revokes all of theirs | **Nothing lists sessions or revokes a single one** — `_revoke_all` is private and all-or-nothing |
| `account.html` | Identity, orders, tier ladder (`pricing.CustomerTier`) exist | **No addresses, no saved payment methods, no notification preferences, no uploaded-model list** |
| `dashboard.html` | Fleet state and order counts exist | **No aggregation layer**: KPIs with period deltas, 12-hour schedule, filament headroom including queue-committed grams, stage funnel |
| `catalog.html` | Nothing — `contexts/catalog` is only `PreparedPlate` | **A model library**: assets, categories, difficulty, ratings, print counts, and measured time/price from the last real print |
| `settings.html` | Nothing — all 100 parameters are constants in code | **A settings store** with typed rows, defaults, revert, and a change audit log |
| `blog.html` · `blog-post.html` | Nothing | **A journal**: posts, categories, contents, publication state |
| `index.html` | — | Component reference, not a product screen |

Two details worth noticing, because they say the kit was written against the real
system rather than imagined:

- Its 100 settings identifiers are the actual names — `margin_percent`,
  `weight_material_headroom`, `guard_tier_cliffs`, `price_variance_tolerance`,
  `telemetry_poll_seconds`. It even lists `sla_sweep_seconds`, which now exists.
- `material_procurement_flat` appears as a settings row. That is the placeholder
  currently hardcoded at 500 ₽; the settings store is where it stops being one.

---

## 3. The plan

Nine slices. Each has an exit criterion in the roadmap's style — a thing that
either happens or does not, not a percentage.

### Slice A — Model and plate storage — **done**
*Unblocked Phase 4's exit criterion; the catalogue and own-models still build on it.*

- `ModelAsset`: the uploaded mesh as a first-class record, content-addressed by
  SHA-256 so re-uploading the same file is free and a plate can name its source
- Blob storage on disk behind a narrow interface, so object storage is a later
  swap and not a rewrite. Not in the database — plates are tens of megabytes
- Upload, download and retention endpoints; `model_retention_days` honoured
- `plate_for` reads real bytes; the prep queue offers the model for download and
  ingests the uploaded plate, parsed by `catalog/plate_file.py`

**Exit:** a paid order for a previously-prepared configuration reaches a real
printer and starts, with no human action, and the assignment record explains which
machines were considered.

### Slice B — The visual language — **done**

- `design/css/*` promoted into `packages/ui/src/harvester/`, split at the
  boundary the kit already drew: `system.css` (sections 01–14, what every screen
  is built from) and `screens.css` (15–19, which style screens that do not exist
  yet and ship with the slice that builds them)
- **Fonts self-hosted, and corrected.** The kit's three faces cannot render
  Russian — Orbitron and Share Tech Mono are Latin-only, Chakra Petch has no
  range covering U+04xx. Each role is now two faces split by `unicode-range`:
  Orbitron + Play, Chakra Petch + Exo 2, Share Tech Mono + JetBrains Mono. Latin
  comes from the kit's own faces, so the design holds where it was drawn
- `DataTable` → `.hv-table`, `StatusTags` → `.hv-tag`, `PriceBreakdown` and
  `DeltaPreview` → `.hv-leader` rows with `.hv-slab` totals
- The navigation overlay as one component fed by `actor.permissions`

**Exit:** every shared component renders in Harvester, and both apps do —
verified at 15–18:1 contrast. The `--pr-*` tokens survive **as aliases onto
`--hv-*`**, which is what carried the language into unconverted screens without a
broken intermediate. Removing them is Slice C's job, not this one: 250 of the 274
remaining references are the two app stylesheets, which are the screens Slice C
restyles. The original exit criterion ("no `--pr-*` left in a shipped
stylesheet") could never have been met here — it describes the end of C.

### Slice C — Screens that only need restyling
Storefront: configurator, checkout, cabinet. Console: order desk, prep, materials,
fleet, users. The data is already there; `js/kit.js` is deleted on contact because
React owns that state.

Concretely this is `apps/web/src/app.css` and `apps/console/src/console.css` —
250 `--pr-*` references between them, each one a rule still written in the old
vocabulary. **Slice B's aliases are deleted when the last one goes**, along with
the bare-control fallbacks in `tokens.css`. A token still resolving through the
bridge is a screen not yet converted, which makes the count an honest progress
measure.

Two genuine additions: the configurator's usage-scenario dialog and resize
control, and per-order margin on the desk.

**Exit:** a customer configures, pays and watches an order through to shipping
entirely in the new language.

### Slice D — The settings store
- A typed settings table: key, value, type, default, section, plus who changed it
  and when. Reads fall back to the code default, so an empty table behaves exactly
  as today
- `RateSnapshot`, `SchedulingPolicy` and `core.config` read through it — with
  pricing purity preserved (ADR-0002): the snapshot is still resolved once and
  passed in, never fetched inside the engine
- The change audit log the kit shows, retained for `audit_retention_days`
- 15 sections, ~100 rows, with revert and the save bar

**Exit:** an owner changes `margin_percent` in the UI and the next quote uses it,
with the old value, the actor and the time recorded.

### Slice E — Recovery, sessions and the account
- Password recovery by six-digit code, not a link — a code cannot be forwarded by
  accident
- Session listing and revocation, which `users.html` shows and no endpoint serves
- Delivery addresses, saved payment methods, notification preferences
- The tier ladder, reading `pricing.CustomerTier`, showing the distance to the
  next tier rather than the badge already held

**Exit:** a customer recovers a password, sees their active sessions, revokes one,
and places an order to a saved address.

### Slice F — The model catalogue
*Depends on Slice A.*

- Categories, tags, difficulty, ratings, print counts
- **Measured** print time and price from the last real print, not an estimate from
  volume — that is the page's whole claim, and it needs Slice A plus completed
  jobs to draw from
- Search, eight sort keys, facets that are OR within a group and AND across groups

**Exit:** a customer finds a model by facet, sees what it actually cost and took
last time, and configures it in two clicks.

### Slice G — The dashboard
- An aggregation layer: KPI tiles with period-over-period deltas for orders,
  printers and finance
- Status wall fed by the existing fleet event stream
- 12-hour schedule with a live `now` line, from printer ETA and queue depth
- Filament headroom as loaded / in stock / **committed to the queue** — the third
  column is the number nobody tracks and the one that causes the stall
- Stage funnel from order status counts

**Exit:** an operator standing across the room can tell which machine needs
attention, and the owner can see where the month's money went.

### Slice H — Post-production and procurement
Roadmap phases 5 and 6, which the kit assumes throughout: the cabinet's nine
stages include post-production and QC, `fleet.html` wants a real service card, and
the dashboard's stage funnel counts stages that do not exist yet.

- `contexts/postproduction`: stages, tasks, timers, consumables, photos, QC
- Maintenance scheduling with real cumulative print hours behind it
- Procurement: suppliers, reorder points, purchase orders, receiving into lots
- True P&L closing back into `RateSnapshot`

**Exit:** the margin the system quotes matches the margin it measures, within a
stated tolerance.

### Slice I — The journal
Entirely independent of everything above, which is why it is last. Posts,
categories, contents sidebar, publication state, reading progress.

**Exit:** an editor publishes a report and it appears with its archive entry.

---

## 4. Sequencing

```
A ──┬─→ F (catalogue)
    └─→ Phase 4 exit, first real print
B ──→ C ──→ (all restyled screens)
D, E, G  independent of A; G is richer after H
H ──→ G's stage funnel and service card become real
I    any time
```

**A and B can run in parallel** — one is backend, the other frontend, and they do
not touch the same files. That is the fastest opening.

**B before C is not negotiable.** Restyling six screens in the current tokens and
then again in Harvester is the same work twice.

**H before G** for the parts of the dashboard that count post-production stages,
though the machine and money halves of the dashboard do not wait for it.

---

## 5. Risks this plan carries

| Risk | Why it is real | Mitigation |
|---|---|---|
| **Blob storage grows without bound** | Every uploaded STL kept forever fills the farm's disk | `model_retention_days` from day one, not retrofitted; content-addressing means duplicates cost nothing |
| **Settings store breaks pricing purity** | A settings row read inside the engine would make pricing depend on I/O and void ADR-0002 | Resolve the snapshot once at the edge and pass it in — the existing contract already forbids the alternative and import-linter enforces it |
| **The catalogue's "measured" claim is unfounded early** | With few completed jobs there is nothing to measure, and falling back to estimates silently would be ADR-0007's defect in a new place | Show the estimate *labelled as an estimate* until a real print exists; never present one as the other |
| **Harvester migration stalls half-done** | Two token sets shipping at once is how a UI ends up permanently inconsistent | Treat Slice B as one unit with a hard exit criterion: no `--pr-*` left |
| **Fonts unavailable on the farm's LAN** | The kit loads three faces from Google Fonts; the deployment is on-prem (ADR-0003) | Self-host inside Slice B, before any screen depends on them |

---

## 6. What this plan does not include

The kit shows no screen for it and the roadmap explicitly excludes it: resin
workflow, multi-tenant, multi-site, native mobile, in-app slicing, AI failure
detection, messenger bots, runtime plugins. Each is reopenable with an ADR.
