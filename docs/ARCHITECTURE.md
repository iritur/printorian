# Printorian — Architecture

Target architecture for the Python backend plus two React web apps: a public storefront and a farm console (ADR-0016).
Read [ANALYSIS.md](ANALYSIS.md) first for what the scenario requires.

---

## 1. Locked decisions

These are settled. Changing one requires a new ADR in `docs/adr/`, not a commit.

| # | Decision | Rationale |
|---|---|---|
| D1 | **One backend, one database, one domain model.** Postgres. No mirrored entities, no sync layer. | "All data interconnected" is a constraint, not a feature; mirroring violates it by construction (ADR-0001) |
| D2 | **One pricing engine**, a pure deterministic function with no I/O | Enables delta preview (C4), reproducible historical prices, and prevents quote/cost divergence |
| D3 | **Backend on-prem** on the farm LAN; public storefront exposed through a reverse proxy | Printer control needs LAN; avoids a distributed-system layer for a single site |
| D4 | **Two web apps, no desktop.** Storefront on internet hosting; console on the farm LAN | Superseded ADR-0004: of the six things only a desktop could supposedly do, five were browser features. See ADR-0016 |
| D5 | **Both clients consume a generated TypeScript API client** from the backend's OpenAPI schema | A hand-written client restates the server's types and drifts invisibly (ADR-0005) |
| D6 | **Slicing is human-gated**, and its output (`PreparedPlate`) is a cached first-class artifact | Decided; the cache is what keeps human-gating from scaling linearly with orders |
| D7 | **Drivers never simulate silently.** Failure → `Offline` + alert. Mock driver refuses to load in production | A system that fails into fiction can hide a non-functional core indefinitely (ADR-0007) |
| D8 | **Alembic is the only schema mechanism.** No hand-written upgraders, ever | Two mechanisms means no artefact answers "what is the schema" (ADR-0008) |
| D9 | **No runtime plugin loading.** Feature modules are ordinary packages toggled by config; only *drivers*, *payment providers*, and *shipping carriers* are pluggable, via entry points | Dynamic loading buys no isolation when the coupling lives in a shared layer (ADR-0009) |
| D10 | **Single-tenant now, tenant-safe seams.** No hardcoded rates, all config injected, no global singletons holding farm state | "My farm first, product later" |
| D11 | **Fleet v2 drives Bambu FDM + AMS only.** Elegoo and others are `manual` driver (tracked, not driven) until their driver lands | Focus the hardest work; the driver interface stays brand-neutral |
| D12 | **RU + EN from day one**, ₽, metric. Money is `Decimal` with an explicit `Money` type — never float | Mixing decimal and binary floating point through a cost stack loses money by rounding |
| D13 | **A feature is "done" only with a passing test referenced.** Status docs mark works / scaffolded / stubbed | Checkbox inflation is the mechanism by which a team stops noticing the core does not work |

---

## 2. System shape

```
                         ┌──────────────────────── farm LAN ────────────────────────┐
                         │                                                          │
  internet               │   ┌──────────────┐         MQTT/TLS:8883   ┌──────────┐  │
     │                   │   │              │◄───────────────────────►│ Bambu    │  │
     ▼                   │   │   BACKEND    │         FTPS:990        │ printers │  │
┌─────────┐   HTTPS      │   │   FastAPI    │────────────────────────►│ + AMS    │  │
│ reverse │──────────────┼──►│   + workers  │                         └──────────┘  │
│  proxy  │              │   │              │                                       │
└─────────┘              │   │  Postgres    │◄──── WS/HTTP ────┐                    │
     ▲                   │   │  Redis       │                  │                    │
     │                   │   └──────┬───────┘            ┌─────┴──────┐             │
┌────┴─────┐             │          │                    │  CONSOLE   │             │
│ customer │             │          │ WS/HTTP            │  SPA       │             │
│ browser  │             │          ▼                    │  (LAN)     │             │
└──────────┘             │   ┌──────────────┐            └────────────┘             │
   (bundle served        │   │ STOREFRONT   │             + fleet, materials        │
    from internet        │   │  SPA         │             + order desk, access      │
    hosting; /api        │   │  (tunnelled) │             + kiosk wall              │
    tunnels back here)   │   └──────────────┘                                       │
                         └──────────────────────────────────────────────────────────┘
```

Everything server-side is one deployable unit (API + workers, separate processes, same codebase). Redis is for pub/sub fan-out and task queueing, not as a second source of truth.

---

## 3. Backend structure — modular monolith, vertical slices

```
backend/
  printorian/
    core/                 # config, db session, Money, units, ids, errors, event bus, auth primitives
    contexts/
      identity/           # users, roles, sessions, customer accounts
      catalog/            # model assets, mesh analysis, SKUs, collections, prepared plates
      pricing/            # THE pricing engine — pure, no I/O            ◄── most important module
      ordering/           # cart, order, options, SLA clock, payment state
      inventory/          # material specs, lots, locations, AMS mapping, procurement
      fleet/              # printers, capabilities, telemetry, maintenance, service cards
      scheduling/         # planner: job × printer matching, wait list, capacity forecast
      production/         # job lifecycle, dispatch, prep queue, progress
      postproduction/     # stages, tasks, QC, packing, shipping
      analytics/          # rollups, amortization, P&L
    drivers/              # printer protocol adapters (pluggable)
    api/                  # FastAPI routers (thin), websocket hub, dependencies, OpenAPI
    workers/              # telemetry poller, scheduler tick, notifier, rollups
  alembic/
  tests/
    unit/  contract/  scenarios/  virtualfarm/
```

**Each context is a package with a fixed shape:**

```
contexts/<name>/
  __init__.py     # the PUBLIC interface — the only thing other contexts may import
  models.py       # SQLAlchemy ORM
  schemas.py      # Pydantic DTOs
  service.py      # use cases (may be split into service/ package)
  events.py       # events this context emits
  policies.py     # business rules worth naming
```

**Isolation rules, machine-enforced by `import-linter`:**

- A context may import `core` and other contexts' `__init__` only. Never another context's `models` / `service` internals.
- `pricing` imports **only** `core`. It touches no database and no clock.
- `drivers` imports only `core` and `fleet`'s public types.
- `api` and `workers` may import any context's public interface; contexts may not import `api`.

Cross-context communication is via the public interface for queries, and the **event bus** for reactions. This is the boundary that makes the contexts real rather than a folder layout.

---

## 4. Domain model — deliberately small

### 4.1 The separations that matter

**`MaterialSpec` vs `MaterialLot`** — a product the shop sells is not a physical reel on a shelf. Conflating them into one `Spool` entity cannot express the scenario's materials table, where status and location belong to the reel and price belongs to the product.

| `MaterialSpec` (catalog level) | `MaterialLot` (physical level) |
|---|---|
| PLA Matte / Bambu / Black `#101010` | this specific spool, 743 g remaining |
| properties, density, temps, TDS link | location: `stock:shelf-B3` or `printer:P1S-02:ams-A:slot-3` |
| **buy price per 1000 m** | lot number, opened-at, dryness, humidity |
| **sell price per cm³** | supplier, purchase order, actual paid price |

The scenario's four status tags — `stock` / `in printer` / `ordered` / `none` — are a **derived rollup over lots** per spec. That's exactly why counters above the table work naturally: they are `COUNT(*) GROUP BY derived_status`.

**`ModelAsset` vs `PreparedPlate`** — the artifact that makes human-gated slicing scale.

```
PreparedPlate
  key: (model_asset, scale, material_spec, printer_profile, plate_layout_hash)
  file: 3MF / gcode blob
  truth: exact print minutes, exact filament grams per slot, layer count
  provenance: sliced_by (user), sliced_at, slicer version + profile version
  status: valid | stale (model or profile changed) | rejected
```

First order of a configuration → prep queue → engineer slices → plate cached. Every later order of that configuration → **fully automatic dispatch**. This is the single most important structural idea in the system.

### 4.2 Aggregate map

```
Customer ──┐
           ▼
        Order ──── OrderLine ──── PriceSnapshot (breakdown + rate_snapshot_id + engine_version)
           │           │
           │           ├── ModelAsset (from library or uploaded)
           │           ├── options: material spec(s), colors[≤4], scale, finishes[], rush
           │           └── PreparedPlate?  (null until prep completes)
           │
           ├── promised_at · decay_policy (+ its pinned rates) · sla_credit  (columns, not an aggregate)
           ├── Payment (YooKassa/CloudPayments, ₽, receipt)
           │
           └── Job* ──── assignment ──► Printer ──── AmsSlot ──── MaterialLot
                  │                        │
                  │                        └── ServiceCard ── MaintenanceOperation (periodicity, materials used)
                  │
                  └── PostProductionTask* ──► QcRecord ──► Package ──► Shipment
```

Roughly 25 aggregates rather than 45 entities. Rule: an entity exists because a use case needs it, and it is introduced in the phase that needs it — not up front.

**The delivery promise is three columns, and the aggregate was rejected rather than skipped.** An earlier version of this map drew an `SlaCommitment` emitting a `PriceCredit`; neither was ever built, and the reason belongs here because the drawing keeps inviting someone to build them. An order has at most one promise and one credit figure, so a second table buys no cardinality — and it would cost the ceiling: `sla_credit <= total` is a CHECK constraint on `orders` only because the credit sits on the same row as the total, and on a money path "never credit back more than was collected" is worth more enforced by PostgreSQL than by convention. The credit is recomputed from the promise on every sweep rather than accumulated, so there is no accrual ledger to give a second aggregate a job either. §5 is the mechanism.

---

## 5. The pricing engine (`contexts/pricing`)

The most carefully guarded module in the system. Pure, deterministic, versioned, no I/O.

```python
def price(spec: PriceSpec, rates: RateSnapshot) -> Breakdown: ...
```

**`PriceSpec`** — everything that affects price: geometry estimate (or exact values from a `PreparedPlate`), material spec + colors, scale, finishes, quantity, rush, delivery, customer price-book tier.

**`RateSnapshot`** — an immutable, hashed, stored bundle of every rate: material sell prices, electricity ₽/kWh, labor ₽/h, printer amortization, packaging, failure buffer %, overhead, margin, discount ladder, rush %.

**`Breakdown`** — a list of typed line items, each carrying its *basis* so the UI can explain itself:

```python
LineItem(code="labor.print_supervision",
         label_ru="Труд · надзор за печатью",
         amount=Money("1_680.00", RUB),
         basis="4.2 ч × 400 ₽/ч",
         category=COST)
```

### What this design buys

| Scenario need | How it falls out |
|---|---|
| C3 transparent structure | `Breakdown` *is* the structure; UI renders line items with basis text |
| C4 delta preview | `diff(price(spec_a, r), price(spec_b, r))` grouped by `code` → "+120 ₽ labor, −260 ₽ material" |
| Reproducible history | Order stores `rate_snapshot_id` + `engine_version`; the exact quote can be recomputed years later |
| Testability | Property tests: monotonic in quantity, discount ladder never inverts, total == Σ items, no float drift |
| Storefront/console agreement | There is one function. There cannot be a second. |

### Estimate → actual variance policy

Because slicing happens after checkout (D6), price is quoted from a mesh heuristic and truth arrives later:

```
EstimateSource: MeshHeuristic  →  PreparedPlate  →  Measured(actual telemetry)
```

Rule: when the prepared plate's cost exceeds the quoted cost by more than `variance_tolerance` (config, default 15%), the job does **not** dispatch. It routes to `PriceReview` with the delta shown. Within tolerance, the quote is binding and the farm absorbs it. Every variance is recorded so the mesh heuristic can be calibrated against real slicer output — the estimator gets measurably better over time.

### SLA / late-delivery discount (C9)

The promise lives on the order itself: `promised_at`, plus a named `decay_policy` drawn from a closed set (`standard` — 5%/day past a 12-hour grace, capped at 30%; `none`; `strict` — 10%/day past 2 hours, capped at 50%). The order stores the policy code **and the three numbers behind it** — `decay_percent_per_day`, `decay_grace_seconds`, `decay_max_percent`, copied out of `POLICIES` at placement. It stored only the code until #74, and `_credit_for` re-read the live dict on every sweep, so editing `standard` re-priced every promise not yet shipped at the new rate on the next pass. That was ADR-0020's trap on the other money path, surviving because an unshipped order has no pinned breakdown to protect it. The credit is now computed from the pinned copy, and an edit to `POLICIES` reaches forwards only; the freeze at dispatch below is no longer the only thing protecting a promise. Orders placed before the columns existed carry nulls and keep the old lookup — their terms were never recorded, and a plausible guess would be worse than the gap (ADR-0007). §4.2 says why these are columns rather than an aggregate.

A worker (`workers/sla.py`) sweeps every order past its promise and still owing work, and **recomputes** `orders.sla_credit` from the promise, the policy and the total — recomputed rather than accumulated, so a pass that runs twice or a process that restarts mid-sweep cannot double-count. The clock stops when the parcel leaves: the credit is frozen on the transition to `shipped`, and no later sweep touches it.

The credit is **not** a line item in the breakdown and cannot be one — the breakdown is pinned at checkout and never recomputed (ADR-0002, ADR-0020), so a figure that grows *after* payment has nowhere to live inside it. It settles in two places instead: revenue reads net it off (`Order.total - Order.sla_credit`, in `ordering/finance.py`), and where the order is already paid, `PaymentsService.refund_sla_credit` returns exactly the outstanding amount through the payment provider. Predictable and capped rather than an ad-hoc discount someone applies by hand, and **audited**. Every movement of the figure appends a row to `sla_credit_entries` (`ordering/credit.py`) carrying the previous value, the new one, and the promise and three decay terms it was derived from — written in the same transaction as the column it records, so an entry cannot commit while the column does not. It was not audited until #75: the sweep overwrote `orders.sla_credit` in place, `refresh_sla_credit` wrote no `order_events` row (the only two writers were placement and status transition), and the `SlaCreditAccrued` it publishes goes to the in-process bus, whose sinks persist nothing — so no prior value of the credit survived anywhere, on a money path. The ledger is a table of its own rather than `order_events` rows because `OrderView` eagerly loads an order's events on every read, `table()` included, and the credit moves on *every* sweep: at the default `sla_sweep_seconds=300` a `standard` promise moves 1 728 times before it reaches its cap, so one page of twenty late orders would have carried thirty-four thousand event rows.

---

## 6. The scheduler (`contexts/scheduling`)

Also a pure function, so it can be tested against fixtures and run in the virtual farm.

```python
def plan(jobs: list[ReadyJob], printers: list[PrinterState], now: datetime,
         policy: SchedulingPolicy) -> list[Assignment | WaitListEntry]: ...
```

**Eligibility filter** (hard constraints): build volume fits, nozzle diameter compatible, required material *type* available, required colors present in mounted AMS slots with sufficient remaining grams, printer not in maintenance/error, brand-specific capability (multi-material requires AMS).

**Job ordering**: due-date risk first, then explicit priority, then job id — the last
purely so two runs of the planner cannot disagree about work already acted on.

**Scoring** (soft, per job, among *eligible* machines): capability waste (do not spend a
multi-material machine on a single-colour job), material headroom (prefer a spool with
room to spare over one that only just covers the plate), printer amortization rate, then
load balance. Every term is a penalty in `[0, 1]` scaled by a configured weight, and the
cheapest machine wins.

> **Corrected in Phase 4.** This section originally listed *changeover cost* and
> *batching affinity* as the first two soft terms. Implementing it showed both are
> unreachable: eligibility already requires the material **and** every requested colour
> to be mounted, so every machine the scorer sees has them and both terms are
> identically zero for every candidate. Weighting a constant cannot change an outcome.
> They are replaced above by terms that actually discriminate between eligible machines.
> Changeover becomes a real cost again once jobs can be planned onto machines that are
> still busy — queue depth, which Phase 4 does not yet do.

**Every assignment writes an `AssignmentDecision`** — candidates considered, why each was rejected, the winning score components. "Why did job #4127 go to P1S-03?" must be answerable from the database — which means recording the machines that lost, and why, not just the one that won.

**No eligible printer → `WaitListEntry`** with `predicted_start` from the capacity model,
surfaced in the customer cabinet (C7). Wait-list entries are re-evaluated on every
relevant event, not only on a timer.

`predicted_start` is set **only when time alone will fix the wait** — a machine that is
busy with a known finish time. It is `None`, with a reason, when the job needs filament
nobody has mounted (nothing knows when a person will walk over with a spool) or when no
machine on the farm can print the plate as configured (waiting will never help; a human
must decide). Naming a start time in those cases would be the queue's version of a driver
inventing telemetry for an unreachable printer.

Triggers: scheduler tick (default 30 s) plus event-driven re-plan on `job.ready`, `printer.became_idle`, `material.mounted`, `order.priority_changed`.

---

## 7. Printer drivers (`drivers/`)

```python
class PrinterDriver(Protocol):
    async def connect(self, conn: ConnectionInfo) -> None: ...
    async def capabilities(self) -> Capabilities: ...
    def telemetry(self) -> AsyncIterator[Telemetry]: ...
    async def upload(self, plate: PreparedPlate) -> RemoteFileRef: ...
    async def start(self, ref: RemoteFileRef, ams_map: AmsMapping) -> JobHandle: ...
    async def pause(self) / resume(self) / cancel(self, reason) -> None: ...
```

| Driver | Transport | Status |
|---|---|---|
| `bambu` | MQTT over TLS `:8883` (LAN access code + serial) for telemetry/commands; **FTPS `:990`** for 3MF upload; SSDP for discovery | **Built in Phase 3** |
| `manual` | none — state advanced by operators in the UI | Built in Phase 3; this is how Elegoo machines are tracked today |
| `mock` | in-process virtual printer with configurable timing and failure injection | Built in Phase 0, for the virtual farm |
| `elegoo` (SDCP), `moonraker`, `prusalink` | — | Phase 7, and their purpose is to **prove the abstraction isn't Bambu-shaped** |

**Rules enforced in code:**
- `mock` raises at import time if `settings.env == "production"`.
- A driver returning stale/unavailable data must raise `DriverUnavailable`; the fleet context maps that to `Offline` and raises an attention event. No fallback data. Ever.
- Every driver ships **contract tests** against recorded protocol fixtures, so a firmware change breaks CI rather than production.

**Known risk:** Bambu firmware has previously restricted third-party LAN control. Mitigation: (a) protocol spike in Phase 0 against real hardware before anything is built on top; (b) `manual` driver as a permanent, first-class fallback so a locked-down printer degrades to *tracked* rather than *broken*; (c) pin known-good firmware on production machines and test upgrades on one printer first.

---

## 8. Events and real-time

An in-process async event bus inside the backend; Redis pub/sub fans out to WebSocket clients. **One event schema** shared by both web apps, generated into TypeScript alongside the API client.

**The Redis half is not optional, and for a while it did not exist.** The API and the workers run as separate containers (`deploy/compose.prod.yml`), each with its own in-process bus, so every event raised by a *sweep* rather than by a request was published into the worker process and stopped there — telemetry state changes, the SLA clock's credits, the post-production and packaging boards. The console's boards refetch on an event and on connect, never on a timer, so in production they loaded once and then sat still while the farm worked. Nothing failed; the screens were simply wrong, which is why it went unnoticed.

`core/relay.py` is that fan-out. Every process publishes the live patterns onto one channel tagged with its own origin; the API subscribes and pushes what arrives to its WebSocket clients, skipping its own origin because those events already reached the hub off the local bus. That origin filter is what makes the design work unchanged with Redis absent — local events still reach local clients, and only the cross-process hop is missing — and it is what will let the API run more than one replica when ADR-0003 stops being true.

Redis stays a transport and not a source of truth: nothing is persisted, nothing is replayed, and a dropped frame is covered by the client's resync-on-connect. A relay that cannot reach Redis logs and keeps serving rather than failing the operation that emitted the event. The release gate proves the hop in the arrangement that ships (`backend/tools/relay_probe.py`).

**One redaction.** `payment.settled` carries an amount, and every holder of `VIEW_PRODUCTION` is entitled to this socket — while the REST API keeps `VIEW_FINANCIALS` deliberately separate from every production permission. The two disagreed about who may see money. The socket has no per-actor filtering, so the amount is stripped on the one path both sources share (`core.relay.REDACTED_FIELDS`); a screen that should show money asks the API, which checks the permission.

```
printer.state_changed   job.progress        job.finished       job.failed
order.stage_changed     material.low        material.mounted   attention.raised
plate.prepared          sla.at_risk         payment.settled
```

The **personnel dashboard is a subscription over `attention.*`** — "print finished, move to post-production" (C10) is not a polling loop, it is an event with an acknowledgement and an escalation timer.

---

## 9. Clients

### Shared frontend packages

```
frontend/
  packages/
    api-client/     # GENERATED from OpenAPI — never hand-written
    events/         # generated event types + typed WS subscription hook
    ui/             # shared React components; design tokens; i18n (RU/EN)
    domain/         # shared formatting: Money, units, statuses, enum labels
  apps/
    web/            # Vite SPA: storefront (configurator, cabinet) + light admin
    console/        # Farm console: fleet, materials, order desk, access (LAN only)
```

### The DataTable component

The scenario asks for the same table pattern at least four times (materials, printers, orders, service operations). It is built **once**, in `packages/ui`, and configured per entity:

```ts
<DataTable
  source={materialsQuery}
  columns={materialColumns}          // sortable headers
  statusTags={materialStatusTags}    // counter chips above the table
  detail={MaterialDetailDrawer}      // popup with full information
  actions={[addMaterial, orderMaterial]}
  savedViews                          // filters persist per user
/>
```

The scenario asks for this table at least four times — materials, printers, orders, service operations. Building it four times is how each one becomes 500 lines and how the four end up behaving differently.

### The two apps (D4)

Both are ordinary React SPAs on `packages/ui`, same-origin with the API, sharing
one session cookie.

**Storefront** (`apps/web`) — catalogue, configurator, checkout, cabinet, journal.
Its bundle is served from internet hosting, which reverse-proxies `/api` back to
the farm. That machine holds no data at rest.

**Console** (`apps/console`) — fleet, materials, order desk, prep, access,
finances, analytics. Served by the on-prem server on the LAN, and never reachable
from the storefront: staff screens are absent from that bundle rather than hidden
inside it.

What the desktop app used to justify itself with, and where each went:

| Was | Now |
|---|---|
| LAN printer discovery | Server-side — the backend is already on the LAN |
| NAS/local model library | Server-side, behind model storage |
| Launching the slicer, watching a folder | A download/upload round trip (ADR-0006, amended). The only genuinely native capability, and one is not a desktop app |
| Kiosk/wall mode | `chrome --kiosk` on a console route |
| Native notifications | Web Notifications API |
| Barcode/QR input | HID scanners type; camera scanning is `getUserMedia` |

**Security:** the console's reach is the API's permission matrix, enforced server-
side. Being unreachable from the internet is a deployment fact, not a boundary —
role gates live in the API layer, because hiding financials in a template is not a
security boundary.

---

## 10. Cross-cutting

| Concern | Decision |
|---|---|
| **Auth** | Session cookie for both apps — each is same-origin with the API, the storefront behind the tunnel and the console on the LAN. Roles: `customer`, `operator`, `engineer`, `manager`, `owner`. **Role gates live in the API layer**, not the UI — hiding a control is not a security boundary |
| **Money** | `Decimal` + explicit `Money(amount, currency)`. Rounding rules centralized. Floats are banned in `pricing` by lint rule |
| **i18n** | RU + EN. Backend returns codes and structured data; clients render labels from message catalogs. **No user-facing strings hardcoded in the backend** |
| **Files** | Model assets and plates on the filesystem/NAS behind an `ObjectStore` interface (local now, S3-compatible later); DB stores references + hashes only |
| **Time** | UTC everywhere internally; farm timezone applied only at presentation and in open-hours rules |
| **Migrations** | Alembic only. One head. Migration tests run on every CI build against a fresh and a seeded database |
| **Observability** | **Logging works.** Structured logs — JSON in production, plain text elsewhere — every line carrying the correlation id of the request that produced it, taken from the caller's `X-Request-ID` when there is one so a trace begun at the proxy continues, and echoed back so a person reporting a problem can quote it (`api.middleware`). One access line per request with its duration, which is the only signal here that can see a slow *request* rather than a slow *query*. **Health works, and the three endpoints answer three different questions.** `/health` touches nothing. `/health/ready` names each dependency separately so an outage names its own cause: `database`, `telemetry_partitions`, `assignment_records`, `printers_listing`, `materials_listing`, `wal_archiving`, and — only where a relay is configured at all — `event_relay`. Only `database` can come back `failed`, and only that returns 503; the rest report `degraded` at 200, because a broken backup, a quiet live board or a table that has outgrown its shape must not be turned into a broken farm. `assignment_records` reports a *threshold* rather than a fault, which is what makes it different from `wal_archiving` beside it: the table ADR-0018 deliberately left unpartitioned, measured out of the catalogue against the 10M-row / 20 GiB trigger, and once crossed it stays lit until the table is split rather than clearing when a condition passes. The two `*_listing` checks are the third shape again: the listings DATABASE-REVIEW §9 left unpaged, counted against a 500-row trigger by a count that stops at the trigger so the probe cannot become the cost, and *clearing on their own* — a set that can shrink, unlike a table that has already been written. **Drivers are deliberately not among them** — the pool of live printer connections belongs to the *worker* process (`workers/drivers.py`, kept alive between passes so a fifty-machine farm does not reconnect every tick), so the API has no connection state to report and a check here would have to invent one. `/health/workers` covers the seven sweeps by name, from a beat each records at the *end* of a pass, and is kept out of readiness on purpose: a wedged sweep is a reason to alert, not to roll a release back. **Metrics are stubbed** — no exporter, no registry, no dependency, and nothing served at `/metrics`. Note the collision before concluding otherwise: `GET /fleet/metrics` and `/fleet/metrics/{printer_id}` are the *farm's* measured occupancy behind `VIEW_PRODUCTION`, not an instrumentation scrape. Stage 5 |
| **Blocking work** | Nothing that computes for a second runs on the event loop. The API is one process, so a second spent parsing a mesh is a second in which it serves nothing — `core.cpu` runs that work in a bounded pool of threads, and the limit is injected rather than assumed |
| **Rate limits** | Ceilings on the endpoints whose *cost* is the problem, and a lockout in front of password guessing (`core.ratelimit`). `POST /pricing/quote` takes an optional actor, so it is reachable without signing in and every call parses a mesh. In-process, which is correct while ADR-0003 holds and is recorded in DATABASE-REVIEW §9 as a trade-off rather than a fact |
| **Request bodies** | Refused by size at the ASGI layer, before anything buffers them (`api.middleware.BodySizeLimitMiddleware`). A check inside a handler runs after `python-multipart` has already parsed the whole body, so it cannot decline to spend the memory |
| **Backup** | `pg_dump` + object-store snapshot on a schedule, with a **tested restore procedure** — untested backups are not backups |

---

## 11. Governance — how this stays clean

Mess is not prevented by intention; it is prevented by CI failing.

| Rule | Enforcement |
|---|---|
| Context isolation | `import-linter` contracts in CI |
| `pricing` has no I/O | import contract: `pricing` may import only `core`; no `sqlalchemy`, no `datetime.now` |
| No file over 400 lines | ruff/custom lint gate |
| Types | `mypy --strict` on `contexts/` and `drivers/` |
| No second pricing/scheduling implementation | code-owner review on `contexts/pricing` and `contexts/scheduling` |
| API client never hand-edited | generated file, CI regenerates and fails on diff |
| Feature marked done ⇒ test exists | PR template requires the test reference (D13) |
| Every driver has contract tests | CI gate: a driver without fixtures does not merge |
| Virtual-farm E2E passes | CI gate on every PR — order → prep → dispatch → print → post-production → ship, with mock printers |

The last one deserves emphasis: **the virtual farm is built in Phase 0, before the features it tests.** It is what lets the scheduler and the order pipeline be developed and regression-tested without hardware, and it is what makes a silently-simulating connector impossible to ship: simulation has a name, a driver, and a refusal to load in production.
