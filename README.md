# Printorian

Management system for an automated 3D print farm (Bambu Lab first): customer-facing configurator with transparent pricing, automatic printer assignment and dispatch, and farm-floor operations.

**Stack:** Python / FastAPI / PostgreSQL backend (on-prem, farm LAN) · two React SPAs — a storefront on internet hosting and a farm console on the LAN (ADR-0016).

## Read in this order

| Document | What it covers |
|---|---|
| [printorian_scenario.txt](docs/printorian_scenario.txt) | The product scenario — source of truth for requirements |
| [docs/ANALYSIS.md](docs/ANALYSIS.md) | What the scenario requires, decomposed into capabilities with the difficulty named |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Locked decisions, module map, pricing engine, scheduler, drivers, governance |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phase plan with exit criteria and risks |
| [docs/adr/](docs/adr/README.md) | The locked decisions, each with the problem that motivated it |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | One agreed name per concept, RU / EN |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Setup, daily commands, the CI gates |
| [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | How it is built, released, run and kept running — containers, IaC, CD, autonomy |

## The four rules

1. **One backend, one database, one domain model.** No mirrored entities, no sync layer.
2. **One pricing engine** — a pure deterministic function. Transparent breakdowns and per-option price deltas fall out of it for free.
3. **Drivers never simulate silently.** A disconnected printer reports `Offline` and raises an alert. It never invents data.
4. **A feature is done only when a test proves it.** Status docs say *works* / *scaffolded* / *stubbed*.

Each is enforced structurally rather than by care — an `import-linter` contract, a
driver interface with no fallback path, a CI gate. See [docs/adr/](docs/adr/README.md)
for why each one is worth that.

## Layout

```
backend/     FastAPI + PostgreSQL — all domain logic lives here
  printorian/core/        config, Money, units, ids, clock, errors, events, db
  printorian/contexts/    bounded contexts (identity; pricing lands in Phase 1)
  printorian/drivers/     printer protocol adapters (mock, manual; bambu in Phase 3)
  printorian/api/         thin HTTP layer — authorization enforced here
  tools/                  governance gates, OpenAPI export, Bambu spike
frontend/
  packages/ui/            design tokens, RU/EN catalogues, the one DataTable
  packages/api-client/    generated from OpenAPI — never hand-edited
```

## Status — Phases 0 and 1 complete

344 tests green (294 backend, 50 frontend), six CI gates enforced.

**Phase 0 — foundations**

| Delivered | |
|---|---|
| `core` | `Money` (Decimal-only), units, UUIDv7 ids, injectable clock, error taxonomy, async event bus |
| `identity` | Users, sessions, the role/permission matrix, Argon2 hashing |
| Drivers | `PrinterDriver` contract, `mock` (refuses production), `manual` (human-driven machines) |
| Virtual farm | Deterministic N-printer harness; full print cycles run in CI |
| Schema | Alembic, one head, verified against real PostgreSQL including downgrade and drift |
| Frontend | `DataTable` with sortable headers, status-tag counters, detail activation; RU/EN catalogues; generated API client |

**Phase 1 — pricing and catalogue**

| Delivered | |
|---|---|
| `pricing` | Pure engine: full cost stack, discount ladder with tier-cliff guard, customer tiers, content-addressed `RateSnapshot`, per-line `diff` |
| `catalog` | STL parsing (binary + ASCII), exact volume, bounding box, watertightness, thin-wall warnings; mesh-heuristic estimator |
| `inventory` | `MaterialSpec` / `MaterialLot` split, shelf and AMS-slot locations, derived status rollup, usage-scenario recommendation |
| API | `POST /pricing/quote`, `POST /pricing/preview`, materials table with status counts |

**Exit criterion met** — an STL upload returns a fully itemized price whose lines sum to
the stated total, and changing one option returns a correct, labelled per-line delta
(scenario steps 3 and 4). Every line code has a Russian and an English label, asserted in CI.

**Phase 0's remaining gate is not code.** [`backend/tools/bambu_spike.py`](backend/tools/bambu_spike.py)
must be run against a real Bambu printer to confirm LAN control works before Phase 3 is
committed to. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#before-phase-3-the-bambu-spike).

Next: **Phase 2 — storefront: configurator, orders, payment** ([docs/ROADMAP.md](docs/ROADMAP.md)).

floor@printorian.example	shop-floor-pass-1
boss@printorian.example	owner-pass-12345 (console-dev-password-123)