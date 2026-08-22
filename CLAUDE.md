# Working in this repository

Standing instructions for Claude. [README.md](README.md) is the map and
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) is the setup — neither is repeated here.
This file is the part that is easy to get wrong.

Current state, what is in flight, and what is deliberately deferred live in
[HANDOFF.md](HANDOFF.md). Read it before starting; update it before finishing.

---

## 1. The gates

These run in CI on every push. Run them locally before saying anything is done.
A failure is a design signal, not a formality — each encodes a specific way this
kind of system rots.

```bash
cd backend
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy                        # strict
./.venv/Scripts/lint-imports.exe                          # architecture contracts
./.venv/Scripts/python.exe tools/check_context_isolation.py
./.venv/Scripts/python.exe tools/check_file_length.py      # 400 lines, hard
```

```bash
cd backend
PRINTORIAN_ENVIRONMENT=test \
PRINTORIAN_DATABASE_URL="postgresql+asyncpg://printorian:printorian@localhost:5433/printorian" \
./.venv/Scripts/python.exe -m pytest -q
```

```bash
cd frontend && npm run typecheck && npm run lint && npm run test && npm run build
```

After **any** change to an API route or response model:

```bash
cd backend && ./.venv/Scripts/python.exe tools/export_openapi.py --out openapi.json
cd frontend && npm run generate:api
```

Postgres is on **5433**, Redis on **6380** — off the defaults on purpose, so a
local install of either is not silently used instead.

## 2. Rules that are not negotiable

The ADRs in [docs/adr/](docs/adr/) are decisions, not suggestions. The six that
actually bite day to day:

| | |
|---|---|
| **ADR-0002** | The pricing engine is pure. No I/O, no clock, no database, no floats. Rates are *given* to it. |
| **ADR-0005** | The TypeScript API client is generated, never hand-edited. |
| **ADR-0007** | **A driver never simulates.** A reading the farm does not have is absent, never zero. This one generalises past drivers — see §4. |
| **ADR-0008** | Alembic is the only schema mechanism. One head. `alembic check` must agree with the ORM. |
| **ADR-0012** | The backend emits error *codes*, never localized prose. Clients render them. |
| **ADR-0021** | Tests run on real PostgreSQL. There is no SQLite fallback and adding one is not a fix. |

**Layering**, enforced by `lint-imports`: `core` imports no context. A context
imports another context's public `__init__` only, never its `models` or `service`.
`api` and `workers` are siblings and may not import each other — shared code goes
to `core`.

**D13**: a feature is done only when a test proves it. No exceptions, including
for "obvious" changes and for anything an agent generated.

**400 lines per file**, hard. Split at a real seam — by responsibility, not by
cutting where the counter tripped.

## 3. How code is written here

Read a neighbouring file before writing a new one. This codebase is unusually
prose-heavy **on purpose**: comments explain *why* a decision was made, what the
alternative was, and what it cost. A comment that restates the line above it is
noise; a comment naming the failure the line prevents is the point.

When you change something load-bearing, say so where the reader will be standing
when they need it — not in a commit message they will not be reading.

## 4. ADR-0007 is the recurring one

It is written about drivers and it governs the whole system: **never invent a
number the farm did not measure.**

Concretely, and each of these has already been got wrong once:

- A null temperature is "not measured", not 0 °C.
- An hour with no telemetry is not an idle hour. It needs a third rendering,
  distinct from both busy and idle.
- An unknown printer id must 404, not return an empty grid — an all-null response
  reads as "this machine did nothing".
- A denominator must be what was *observed*, never the roster. Otherwise the worse
  the coverage, the healthier the farm looks, and the error is silent.

Money is the same rule wearing different clothes: `VIEW_FINANCIALS` is kept
separate from every production permission, so a response carrying seconds must not
quietly start carrying rubles or kilowatt-hours.

## 5. Traps

Real ones, each of which has already cost time.

**Piping hides failure.** `cmd | tail` returns *tail's* exit code. `npm ci | tail`
reported success while the install had failed, and the checks after it ran against
a stale `node_modules`. Always:

```bash
cmd > /tmp/out.log 2>&1; echo "exit=$?"
```

**The console's dashboard types are hand-written.** `apps/console/src/dashboard/types.ts`
mirrors `GET /dashboard` by hand, by convention. Regenerating the client does **not**
cover it, so removing a backend field leaves `tsc` green and the screen rendering
`undefined`. Change both.

**Do not run two backend test sessions at once.** They share `printorian_test` and
truncate each other's tables; the result is failures that do not reproduce.

**The docs go stale.** `DESIGN-KIT-PLAN.md` and `DESIGN-KIT-INTEGRATION.md` have
both described built features as missing. Verify against the code before repeating
a status from any document — including this one.

**Reachability is a question about the bundle, not the source.** Grepping the
source for a class name cannot tell a live `className` from the same word in a
message catalogue. Check `apps/*/dist/assets/*.js` after a build.

## 6. Dependency holds

`.github/dependabot.yml` holds exactly one thing, and the reason is recorded there:
**TypeScript major**. TS 7 is the Go rewrite; `openapi-typescript` crashes on it
(`ts.factory` is undefined) and it is the only route to the API client. Three
workarounds were tried and all fail — the comment lists them so nobody repeats the
search. Lift it when `npm view openapi-typescript peerDependencies` accepts 7.

Nothing else is held. Prefer removing a redundant pin over adding an ignore: an
ignore silences a symptom, and a pin that some other package already fixes exactly
is a conflict waiting to happen.

## 7. Do not "fix" these

Deliberate decisions that look like omissions:

- **`customer_storage_quota_bytes` is displayed, not enforced.** Refusing a quote
  mid-configuration is the wrong behaviour; the setting's comment says so.
- **Rate limiting is in-process.** Correct while the deployment is one API process
  (ADR-0003). Recorded in `docs/DATABASE-REVIEW.md` §9 with the other trade-offs.
- **The storefront's `body` rule lifts the page ground** from `--hv-void` to
  `--hv-bg`. It predates Harvester and changing it is a visual decision, not a
  cleanup.

If one of these looks wrong, raise it — do not silently change it.

## 8. Verify, then say so

Report what happened, not what should have happened. If a check did not run, say
it did not run. If tests fail, quote the output. "Should work" is not a result, and
an agent's report of its own success is not evidence — reproduce it.
