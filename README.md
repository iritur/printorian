# Printorian

Management system for an automated 3D print farm (Bambu Lab first): a customer-facing
configurator with transparent pricing, automatic printer assignment and dispatch, and
farm-floor operations.

**Stack:** Python / FastAPI / PostgreSQL on the farm LAN · two React SPAs — a storefront
on internet hosting and a farm console on the LAN (ADR-0016).

## Run it

```bash
run_web.bat        # database, API, workers, storefront on :5173
```

`run_console.bat` adds the farm console on :5174, reusing anything already up.
`run_design.bat` serves the static design kit on :4180. First-time setup — virtualenv,
npm install, migrations — is in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Read in this order

Working on this with an AI agent? [CLAUDE.md](CLAUDE.md) carries the rules that hold
everywhere, with the area-specific ones beside the code they govern —
[backend/CLAUDE.md](backend/CLAUDE.md) and [frontend/CLAUDE.md](frontend/CLAUDE.md),
so a session only loads what it needs. [HANDOFF.md](HANDOFF.md) carries the current
state: what just changed, what is deliberately unfinished, and what needs a person
rather than an agent.

| Document | What it covers |
|---|---|
| [printorian_scenario.txt](docs/printorian_scenario.txt) | The product scenario — source of truth for requirements |
| [docs/ANALYSIS.md](docs/ANALYSIS.md) | What the scenario requires, decomposed into capabilities with the difficulty named |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Locked decisions, module map, pricing engine, scheduler, drivers, governance |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phase plan with exit criteria and risks |
| [docs/adr/](docs/adr/README.md) | 21 locked decisions, each with the problem that motivated it |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | One agreed name per concept, RU / EN |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Setup, daily commands, the CI gates |
| [docs/DATABASE-REVIEW.md](docs/DATABASE-REVIEW.md) | Schema by context, indexing, partitioning, and the accepted trade-offs |
| [docs/DESIGN-KIT.md](docs/DESIGN-KIT.md) | Which of the twenty-one kit screens exist, and what the five that do not would need |
| [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Containers, release gate, deployment stages |
| [docs/RUNBOOK-BACKUP-RESTORE.md](docs/RUNBOOK-BACKUP-RESTORE.md) | WAL archiving, base backups, the restore drill |
| [docs/RUNBOOK-PAYMENTS.md](docs/RUNBOOK-PAYMENTS.md) | YooKassa and T-Pay: method selection, demo-store vs live-store testing, test cards |
| [docs/RUNBOOK-FIRST-PRINT.md](docs/RUNBOOK-FIRST-PRINT.md) | Proving the driver against a real printer — the largest unproven assumption in the system |
| [docs/BAMBU-LAN-PROTOCOL.md](docs/BAMBU-LAN-PROTOCOL.md) | Spike findings from a real printer — the spec the Phase 3 driver is built from |

## The four rules

1. **One backend, one database, one domain model.** No mirrored entities, no sync layer.
2. **One pricing engine** — a pure deterministic function. Transparent breakdowns and
   per-option price deltas fall out of it for free.
3. **Drivers never simulate silently.** A disconnected printer reports `Offline` and
   raises an alert. It never invents data.
4. **A feature is done only when a test proves it.** Status docs say *works* /
   *scaffolded* / *stubbed*.

Each is enforced structurally rather than by care — an `import-linter` contract, a driver
interface with no fallback path, a CI gate. See [docs/adr/](docs/adr/README.md) for why
each one is worth that.

## Layout

```
backend/
  printorian/core/        config, Money, units, ids, clock, errors, events, db
                          cpu (blocking work, off the loop) · relay (events across
                          processes) · ratelimit · heartbeat
  printorian/contexts/    catalog · fleet · identity · inventory · journal
                          ordering · payments · pricing · production · scheduling
  printorian/drivers/     printer adapters — bambu, manual, mock
  printorian/api/         thin HTTP layer; authorization enforced here
  tools/                  governance gates, OpenAPI export, Bambu spike
frontend/
  apps/web/               storefront — catalogue, configurator, checkout, journal
  apps/console/           farm console — orders, prep, library, journal, fleet,
                          materials, users
  packages/ui/            Harvester design system, RU/EN catalogues, DataTable
  packages/api-client/    generated from OpenAPI — never hand-edited
  packages/events/        the WebSocket event stream both apps subscribe to
deploy/                   production compose and the Caddyfile the release gate runs
design/                   the static design kit the UI is built against
```

## Status

CI runs three jobs: six governance gates plus tests and migrations on the backend,
typecheck/lint/test/build on the frontend, and a release gate that builds both images,
brings the production compose up and proves migrations, readiness, same-origin proxying,
that every worker loop is sweeping, and that a live event raised in one container reaches
the other — before scanning and signing.

Built and working end to end: pricing engine and quote/preview API · STL analysis and the
model catalogue with staff curation · inventory with AMS-slot locations · the storefront
configurator, checkout with delivery and payment, order tracking with the nine-stage
pipeline and queue position, and the customer
account — profile, loyalty ladder, addresses, uploads, receipts and sessions · the farm
console · the journal with an RSS feed · fleet, production, scheduling and the virtual
farm harness · one-image containers with a release gate (INFRASTRUCTURE Stage 1).

### Load-bearing behaviour worth knowing about

- **Blocking work never runs on the event loop.** The API is one process, so a second
  spent parsing a mesh is a second in which it serves nothing — not the storefront, not
  the console, not the health check. `core/cpu.py` runs that work in a bounded thread
  pool, and carries the measurements that make it a bug rather than a preference.
- **Live events cross the process boundary.** The API and the workers are separate
  containers with an in-process bus each, so everything a *sweep* raises reaches a
  watching console through the Redis relay in `core/relay.py` — without it those events
  die in the worker and the boards go quiet.
- **The endpoints that cost something have ceilings.** `POST /pricing/quote` takes an
  optional actor and parses a mesh, so it is rate-limited; sign-in has a lockout; request
  bodies are refused by size before anything buffers them.
- **A worker that is running but not working is visible.** Each loop beats at the end of
  every pass, and `python -m printorian.workers --check` is the container's healthcheck.

Two things are **not** proven, and both need hardware or an account this repository
cannot supply:

- The Bambu LAN spike ran against a real X2D and its findings are the specification
  Phase 3 builds from — telemetry and control proven, the FTPS *write* still pending
  storage in the test machine
  ([docs/BAMBU-LAN-PROTOCOL.md](docs/BAMBU-LAN-PROTOCOL.md)).
- Payments run against the `mock` and `manual` providers. The YooKassa adapter exists but
  has never been exercised against the real gateway in test mode, which is what
  [Phase 2's exit criterion](docs/ROADMAP.md) asks for.

Next: [docs/ROADMAP.md](docs/ROADMAP.md) for the phase plan,
[docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) for deployment Stage 2 onwards.
