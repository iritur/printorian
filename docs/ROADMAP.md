# Printorian — Roadmap

Phased build plan. Each phase has **deliverables**, an **exit criterion** (a demonstrable capability, not a checkbox), and named risks.

Sequencing principle: **build in customer-value order, but de-risk in uncertainty order.** The Bambu protocol is the highest technical unknown, so it is spiked in Phase 0 even though the driver ships in Phase 3. The mistake to avoid is discovering that the core integration does not work after everything has been built on top of it.

**Current state and the work after Phase 7** live in
[DESIGN-KIT-PLAN.md](DESIGN-KIT-PLAN.md) — a per-phase status read off the code
rather than off this document.

**The full integration plan** for the twenty-one screens in
[`design/`](../design/README.md) — every list, table, filter and parameter,
checked against the code — is
[DESIGN-KIT-INTEGRATION.md](DESIGN-KIT-INTEGRATION.md).

---

## Phase 0 — Foundations and de-risking

Nothing user-facing. This phase exists so the next six don't rot.

**Deliverables**
- Repo skeleton per [ARCHITECTURE §3](ARCHITECTURE.md); Postgres + Redis via docker-compose; Alembic wired with one head
- CI from commit one: ruff, `mypy --strict`, `import-linter` contracts, file-length gate, pytest, migration test on a fresh DB
- `core`: `Money`, units, IDs, config, error taxonomy, event bus, structured logging
- `identity`: users, roles (`customer` / `operator` / `engineer` / `manager` / `owner`), sessions; role gates as FastAPI dependencies
- OpenAPI → TypeScript client generation, wired into CI with a fail-on-diff check
- `packages/ui` bootstrap: design tokens, i18n (RU/EN) scaffolding, and the **DataTable** component
- `mock` driver + **virtual farm harness**: N configurable virtual printers with realistic timing and injectable failures
- ADR set from [ARCHITECTURE §1](ARCHITECTURE.md#1-locked-decisions) written down
- Domain glossary (RU/EN) — one agreed name per concept, so `Spool`/`Material`/`Filament` never diverge again

**Spike, run in parallel and time-boxed:**
> **Bambu LAN protocol spike.** Against a real X1C/P1S: MQTT/TLS `:8883` connect with access code + serial, parse `device/{serial}/report`, read AMS slot state, upload a 3MF over FTPS `:990`, trigger a print, observe progress to completion. Record protocol fixtures for contract tests.

**Exit criterion:** `pytest` runs a virtual farm of 5 mock printers end to end in CI, **and** the spike has physically started and completed one print on a real Bambu printer from a Python script.

**Risk:** if the spike fails (firmware lockdown, auth changes), the automation scope changes materially — that must be known now, not in month four. Fallback: `manual` driver becomes the primary path and "auto-dispatch" becomes "auto-assign + operator confirms."

---

## Phase 1 — Pricing engine and catalog

The customer-facing differentiator, and pure logic with zero hardware risk.

**Deliverables**
- `contexts/pricing`: `price(spec, rates) -> Breakdown`, `RateSnapshot` versioning, `diff()` for delta preview
- Full cost stack, derived and tested from first principles: material, electricity, labor, depreciation, post-processing, packaging, shipping, failure buffer, overhead, marketplace fee, margin
- Declarative quantity discount ladder; rush surcharge; price-book tiers
- Property tests: total == Σ line items, monotonic in quantity, discount ladder never inverts, no float anywhere, RU/EN label coverage for every line-item code
- `contexts/catalog`: model upload, mesh analysis (volume, bounding box, manifold/watertight check, wall-thickness warning), thumbnails, tags, collections
- Mesh-based print time/weight estimator (`EstimateSource.MeshHeuristic`) with a calibration table
- `contexts/inventory`: `MaterialSpec` + `MaterialLot` + locations, with a seeded filament catalogue
- Usage-scenario → material recommendation (C2): property-based matching against in-stock specs

**Exit criterion:** an API call prices a real STL with options and returns a full itemized breakdown; a second call with one option changed returns a correct, labelled delta. Both proven by tests, no UI required.

---

## Phase 2 — Storefront: configurator, orders, payment

**Deliverables**
- Web configurator: model pick/upload, 3D preview, material by type **or** usage-scenario dialog, up to 4 colors, scale, post-processing options, quantity, rush
- **Live transparent price panel** with itemized breakdown and basis text (C3)
- **Delta preview on every option** — "+120 ₽ labor, −260 ₽ material" before the customer commits (C4)
- `contexts/ordering`: cart → order, price snapshot pinned to the order, order state machine
- Registration, login, customer cabinet
- Payment: YooKassa or CloudPayments, ₽, with 54-ФЗ receipt handling and a refund path (needed by the SLA credit)
- `SlaCommitment` with `promised_at` and a declarative decay policy; the credit mechanism itself
- Admin: orders DataTable with status-tag counters, sortable headers, detail drawer

**Exit criterion:** a customer configures a model, sees the price change explained line by line as they toggle options, registers, pays with a real gateway in test mode, and the paid order appears in admin with a pinned, reproducible price snapshot.

**Note:** the SLA *decay policy* ships here; the *clock that trips it* needs real production timing and is validated in Phase 4.

---

## Phase 3 — Fleet, real printer control, farm console

The hardest phase. The spike from Phase 0 becomes a product-grade driver.

**Deliverables**
- `drivers/bambu`: MQTT/TLS telemetry + command, FTPS plate upload, AMS slot state, SSDP discovery, reconnection with backoff, contract tests against Phase-0 fixtures
- `drivers/manual`: operator-advanced state — how Elegoo machines are tracked until their driver lands (D11)
- `contexts/fleet`: printer registry, capabilities (build volume, nozzle, AMS presence), live state, telemetry retention + rollups, amortization model (idle vs printing), cumulative print hours
- Service cards: maintenance operations, periodicity, materials consumed (scenario M3)
- Materials ↔ AMS mapping: which physical `MaterialLot` sits in which printer's which slot (scenario M1)
- Telemetry → event bus → WebSocket; printers DataTable with live status incl. `printing + ETA` (scenario M2)
- **Farm console** (`apps/console`, LAN only): printer detail, live wall view in a kiosk browser, browser notifications. LAN discovery and the model library moved server-side, where the LAN already is (ADR-0016)
- Materials DataTable with derived status tags: `stock` / `in printer` / `ordered` / `none` (scenario M1)

**Exit criterion:** the console shows live state for every real printer on the farm; an operator uploads a plate and starts a print from Printorian; a driver disconnection produces `Offline` plus an alert — and never fabricated data.

**Risks:** Bambu firmware changes (mitigations in [ARCHITECTURE §7](ARCHITECTURE.md#7-printer-drivers-drivers)); FTPS implicit-TLS quirks; MQTT reconnect storms with many printers — cap concurrent connections and jitter reconnects.

---

## Phase 4 — Prep queue, scheduler, auto-dispatch, wait list

This phase is where Printorian becomes the thing the scenario describes.

**Deliverables**
- **Prep queue**: paid orders route to an engineer; the console offers the model for download and takes the sliced plate back as an upload, which the server parses (ADR-0006, amended)
- **`PreparedPlate` cache** keyed by (model, scale, material, printer profile) — repeat orders skip prep entirely and dispatch automatically ([ARCHITECTURE §4.1](ARCHITECTURE.md#41-the-separations-that-matter))
- **Estimate-vs-actual variance policy**: plates outside tolerance route to `PriceReview` instead of dispatching; every variance recorded and fed back into estimator calibration
- `contexts/scheduling`: `plan()` with hard eligibility filters + soft scoring, `AssignmentDecision` audit records, wait list with `predicted_start`, event-driven re-planning
- `contexts/production`: job lifecycle, automatic dispatch to the driver, progress tracking, failure handling and remake
- Customer cabinet: live progress with the full workflow, position in queue, predicted start when wait-listed (C7, C9)
- **SLA clock live**: late orders accrue credits automatically, visible to both customer and management

**Exit criterion:** a paid order for a previously-prepared configuration goes from payment to a printer starting the job **with no human action**, and the assignment record explains which printers were considered and why this one won. An order with no capable printer wait-lists with an honest predicted start.

---

## Phase 5 — Post-production, personnel, fulfilment

**Deliverables**
- `contexts/postproduction`: stages (shelf → assembly → priming → painting → finishing), per-stage tasks, timers, consumables, photos
- **Personnel dashboard** driven by `attention.*` events with acknowledgement and escalation — print-finished alerts (C10)
- Floor stations: console routes that are touch-first and scan-first, with no financial data
- QC: checklists, pass/fail, rework loop back to the correct stage
- Packing and shipping: packages, labels with order QR, carriers, tracking, delivery confirmation
- Customer-visible overall workflow progress across the whole pipeline (C9)

**Exit criterion:** a finished print raises an alert on the floor within seconds, an operator moves it through post-production and QC on a touch station, and the customer's cabinet reflects each transition live.

---

## Phase 6 — Management depth and analytics

**Deliverables**
- Procurement: suppliers, reorder points, purchase orders, receiving into lots, price history per 1000 m (scenario M1)
- Maintenance scheduling with due alerts driven by real cumulative print hours
- Analytics: utilization, success rate, failure taxonomy by printer/material/model, post-production cycle time, material yield and waste, capacity forecast vs booked
- **True P&L**: real amortization, real electricity from telemetry, real labor from stage timers — closing the loop back into `RateSnapshot` so pricing is calibrated by measured reality rather than guesses
- Estimator calibration report: mesh heuristic vs sliced truth, per material and geometry class

**Exit criterion:** the margin the system quotes matches the margin the system measures, within a stated tolerance — and where it doesn't, the report says why.

---

## Phase 7 — Hardening and expansion

**Deliverables**
- `drivers/elegoo` (SDCP) — and its real purpose is to **prove the driver abstraction isn't Bambu-shaped**; `moonraker` / `prusalink` as cheap confirmations
- Backup and **tested restore**; disaster-recovery runbook
- Load testing at realistic printer and order counts; MQTT connection ceiling established
- Security review: auth, payment flow, file upload handling, the storefront tunnel, reverse-proxy exposure
- Operational docs: runbooks written from real incidents, not speculation

**Exit criterion:** a restore-from-backup drill succeeds on a clean machine, and a second printer brand is driven through the identical interface with no changes to `scheduling` or `production`.

---

## Sequencing notes

**Why pricing before printers.** Pricing is pure logic with no hardware dependency — it can be finished and proven while the Bambu spike is still uncertain. It also produces the most visible customer value per unit of work. The hardware risk is handled separately by the Phase-0 spike, not by building hardware first.

**Why the scheduler comes after the driver.** A scheduler that dispatches to a fake driver is a scheduler you cannot trust — every assignment it makes is unfalsifiable.

**Why the virtual farm is Phase 0.** It's the harness that makes Phases 4–5 developable without occupying real printers, and it is the specific mechanism that keeps a silently-simulating connector from ever being shippable.

**Management tables are not a phase.** The scenario's materials/printers/orders tables land inside the phase that owns their data (1, 3, 2 respectively), each built on the single Phase-0 DataTable component. Building them as a separate "management" phase is how four bespoke, divergent screens happen.

---

## Standing risks

| Risk | Mitigation |
|---|---|
| **Bambu firmware restricts LAN control** | Phase-0 spike before commitment; `manual` driver as permanent first-class fallback; pin firmware, test upgrades on one machine |
| **Human slicing becomes the bottleneck** | `PreparedPlate` cache makes it a one-time cost per configuration; measure prep-queue depth from Phase 4 and escalate to headless slicing if it saturates |
| **Mesh estimate diverges from sliced truth** | Variance tolerance gate + recorded variances + calibration report (Phase 6). The estimator is expected to be wrong at first and to improve measurably |
| **Scope creep** | ADRs for locked decisions; anything not tracing to a scenario requirement needs an explicit decision. Messenger bots, animated icons and plugin hosts are the canonical examples of what not to build before the spine works |
| **Payment/54-ФЗ compliance complexity** | Isolate behind a provider interface in Phase 2; treat fiscalization as its own scoped task, not an afterthought of checkout |
| **Single on-prem server is a single point of failure** | Tested restore drill (Phase 7); UPS; degraded mode where printers keep printing while the server is down and reconcile on reconnect |

---

## What is explicitly out of scope

Resin workflow (wash/cure, vat tracking, exposure profiles), multi-tenant SaaS, multi-site, mobile native apps, an in-app slicer, AI camera failure detection, messenger bots, and a runtime plugin system. Each is reopenable with an ADR; none of them are on the critical path to the scenario.
