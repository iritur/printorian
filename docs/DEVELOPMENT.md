# Development

## Prerequisites

Python 3.13+, Node 24+, Docker.

**Docker is required to run the backend tests**, not only to run the app: the
suite runs against real PostgreSQL and fails rather than skipping when none is
reachable (ADR-0021). `docker compose up -d postgres` is enough.

## First run

```bash
docker compose up -d
```

```bash
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]" -c constraints.txt
```

`-c constraints.txt` matters: `pyproject.toml` declares floors (`>=`), and the
constraints file says which versions the image, CI and your machine all actually
install. Without it three people can pass the same gates against three different
dependency sets. Regenerating it is a deliberate act — see the file's own header.

```bash
cd backend && .venv/Scripts/python -m alembic upgrade head
```

```bash
cd frontend && npm install
```

### Seeded accounts

The development database carries two accounts, created by hand rather than by a
seed script. Nothing creates them anywhere else — but nothing refuses them either:
`is_production` currently gates only the mock driver, the mock payment provider
and log formatting, so a farm restored from a developer dump would keep them.
Provisioning the farm's first owner belongs with Stage 2.

| Account | Password | Role |
|---|---|---|
| `boss@printorian.example` | `owner-pass-12345` | owner — the console, every screen |
| `floor@printorian.example` | `shop-floor-pass-1` | operator — production screens only |

Two roles rather than one on purpose: most authorization bugs only show up when
somebody who *should not* see a screen opens it, and that needs an account that
cannot.

Content for the catalogue, materials and journal comes from
`backend/scripts/seed_*.py`, each idempotent.

## Every day

Run the API:

```bash
cd backend && .venv/Scripts/python -m uvicorn printorian.api.app:create_app --factory --reload
```

Run the background workers, in a second terminal:

```bash
cd backend && .venv/Scripts/python -m printorian.workers
```

Separate from the API on purpose. A sweep holding a database session should not
compete with request handling for the same event loop, and a deployment running
several API workers behind a proxy would otherwise run one copy of every clock
per worker — each recomputing the same credits and publishing the same events.

Hosts four loops, each with its own session per pass:

| Loop | Interval setting | What it does |
|---|---|---|
| `scheduler` | `scheduler_tick_seconds` (30) | Plans ready work onto machines and dispatches it. Also wakes on `job.ready`, `printer.became_free` and fleet state changes, so a machine finishing a second after a tick does not idle for the rest of it |
| `telemetry` | `telemetry_poll_seconds` (5) | Asks every reachable machine what it is doing; unreachable ones are recorded offline, never left showing a stale happy state |
| `sla` | `sla_sweep_seconds` (300) | Recomputes lateness credits, so a customer sees what they are owed while still waiting rather than only once the parcel ships |
| `postproduction` | `postproduction_sweep_seconds` (60) | Turns finished prints into floor work and ends the drying timers. **Reconciling, not reactive**: it asks which succeeded prints have no task yet, so a missed tick costs latency and never a lost batch |
| `maintenance` | `maintenance_sweep_seconds` (3600) | Partitions, retention, expired sessions |

The two that talk to printers share one **driver pool**, which keeps connections
alive between passes. Rebuilding a driver per tick would reconnect per tick — on a
fifty-machine farm that is a reconnect storm, and a Bambu driver holds an MQTT/TLS
session. A connection is replaced only when the printer's brand, mode, host,
serial or access-code-set state changes; renaming a printer does not disturb it.

`run_web.bat` starts the worker process alongside the API.

Run the tests:

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

```bash
cd frontend && npx vitest run
```

## Regenerating the API client

The TypeScript client is generated from the backend's OpenAPI schema and is never
hand-edited (ADR-0005). After changing any route or DTO:

```bash
cd backend && .venv/Scripts/python tools/export_openapi.py --out openapi.json
```

```bash
cd frontend && npm run generate:api
```

## The gates

These run in CI on every push. Run them locally before opening a PR — each one encodes a
specific way this kind of system rots, so a failure is a design signal, not a formality.

```bash
cd backend && .venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check .
```

```bash
cd backend && .venv/Scripts/python -m mypy
```

```bash
cd backend && .venv/Scripts/lint-imports
```

```bash
cd backend && .venv/Scripts/python tools/check_context_isolation.py
```

```bash
cd backend && .venv/Scripts/python tools/check_file_length.py
```

| Gate | What it prevents |
|---|---|
| `ruff` / `ruff format` | Style drift; naive datetimes; `print` in library code |
| `mypy --strict` | Untyped seams between contexts |
| `lint-imports` | Layer violations; **pricing purity** (ADR-0002); drivers depending on contexts |
| `check_context_isolation.py` | One context reaching into another's `models` or `service` |
| `check_file_length.py` | The 1,096-line service. Limit is 400 lines |
| `alembic check` | Migrations drifting from the ORM models (ADR-0008) |

Two more run only in the release gate, because they are about the *deployment*
rather than the source tree:

| Gate | What it prevents |
|---|---|
| `python -m printorian.workers --check` | A worker container that is running and not sweeping. It reads the beat each loop records at the end of a pass, so it fails for a wedged loop — which a process check cannot see |
| `tools/relay_probe.py` | Live events not crossing the process boundary. The API and the workers are separate containers with a bus each; the Redis URL, channel and network between them are properties of the arrangement, not of the code |

## Database

Postgres runs on **5433** and Redis on **6380** — deliberately off the default ports so a
locally installed Postgres does not shadow the project's.

Migrations are the only schema mechanism. To add a table:

```bash
cd backend && .venv/Scripts/python -m alembic revision --autogenerate -m "add materials"
```

Read the generated file before committing it. Autogenerate is a first draft, not an author.
Every model module must be imported in `alembic/env.py` or its tables will be proposed for
deletion.

Four things autogenerate cannot see, so they are always hand-written:

* **`USING` clauses.** A type change like `json → jsonb` needs one, and without it the
  `ALTER` simply fails.
* **Partitioning.** `telemetry_samples` is created partitioned in raw SQL, and its
  child partitions are filtered out of the comparison in `env.py` — otherwise every
  month's partition reads as a table the models forgot (ADR-0018).
* **Sequences.** `order_number_seq` is created explicitly.
* **`op.f()` on a drop.** `op.drop_constraint("ck_orders_total_non_negative", ...)` runs
  the name through the naming convention *again* and looks for
  `ck_orders_ck_orders_total_non_negative`. Wrap already-final names in `op.f()`.

### Widening an enum on a large table

Enum columns are `VARCHAR` with no CHECK (see `core.db.enum_column` for why), so adding
a member is usually just a code change. If you ever *do* add a CHECK to a large table,
never do it in one statement — a plain `ADD CONSTRAINT` takes `ACCESS EXCLUSIVE` and
scans every row, which is seconds on `orders` and minutes on `job_events`:

```sql
ALTER TABLE job_events ADD CONSTRAINT ck_job_events_x CHECK (...) NOT VALID;
ALTER TABLE job_events VALIDATE CONSTRAINT ck_job_events_x;
```

`NOT VALID` takes the strong lock only briefly and skips the scan; `VALIDATE` does the
scan under a weak lock that readers and writers can work alongside.

### Backups

Not a development concern, but the schema decisions assume it exists:
[RUNBOOK-BACKUP-RESTORE.md](RUNBOOK-BACKUP-RESTORE.md).

## Testing layout

| Path | Purpose |
|---|---|
| `tests/unit/` | Pure logic — money, permissions, identity. SQLite-backed, fast |
| `tests/api/` | HTTP behaviour, especially the authorization boundary |
| `tests/contract/` | The contract **every** printer driver must satisfy |
| `tests/virtualfarm/` | End-to-end farm runs against mock printers |
| `tests/test_migrations.py` | Real Alembic against real PostgreSQL. Auto-skips without Docker |

The virtual farm is deterministic: it advances a `FixedClock` rather than sleeping, so a
farm-day of printing runs in milliseconds and never flakes.

## The farm console

The staff app (ADR-0016): the farm summary, post-production, fleet, materials,
the order desk and access. It is served from the on-prem server on the farm LAN, so — like the
storefront behind its tunnel — it is **same-origin with the API** and uses the
same session cookie. No CORS, no bearer token, no `PRINTORIAN_CORS_ORIGINS`.

The summary is one request, `GET /dashboard`, assembled across five contexts in
`api/routers/_dashboard_model.py`. Every panel on it is read against a single
instant on purpose: nine concurrent requests would let the KPI tiles and the
status wall disagree by a few seconds, which on a screen whose whole job is
"what is happening right now" is worse than being a moment slower. It needs
`view_all_orders`, not `view_production` — it carries revenue, spend and margin
beside the machine states, so an operator entitled to walk the fleet screen is
not thereby entitled to the farm's finances.

Post-production is the floor's own screen and needs only `view_production`, with
`advance_postproduction` to move a task and `record_qc` to pass or fail one.
Nothing financial appears on it. Its board is likewise one request,
`GET /postproduction/board`.

```bash
cd frontend && npm run dev --workspace @printorian/console
```

It runs on **5174**, deliberately not the storefront's 5173, so both can run at
once — which is now the normal case rather than the exception. `run_console.bat`
starts the database, the API, the workers and this, reusing anything `run_web.bat`
already started.

`PRINTORIAN_API_URL` points the dev proxy at a different backend (default
`http://localhost:8000`); `PRINTORIAN_CONSOLE_PORT` moves the dev server.

A wall display is this app in a kiosk browser:

```bash
chrome --kiosk http://farm-server:5174/
```

### What the two apps share

`packages/ui` holds everything both need: the DataTable, the pricing components,
i18n, the session provider and sign-in panel, and `OrdersScreen` — one orders
table that the storefront composes with a queue position and the console composes
with the desk that advances and refunds. Copying that screen into two apps is how
its three real differences would quietly become ten.
