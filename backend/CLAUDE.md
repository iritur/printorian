# Working in `backend/`

Applies to the Python side. Cross-cutting rules — ADR-0007, D13, the comment
style, the exit-code trap — are in the root [CLAUDE.md](../CLAUDE.md) and are not
repeated here.

## Gates

All six run in CI on every push. Run each separately and read its **own** exit
code — see the root file on why piping hides failure.

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
PRINTORIAN_ENVIRONMENT=test \
PRINTORIAN_DATABASE_URL="postgresql+asyncpg://printorian:printorian@localhost:5433/printorian" \
./.venv/Scripts/python.exe -m pytest -q
```

The full suite is ~1100 tests and takes about fifteen minutes. Budget for it
rather than skipping it.

Postgres is on **5433**, Redis on **6380** — off the defaults on purpose, so a
local install of either is not silently used instead. Both come from
`docker compose up -d postgres redis`.

## The ADRs that bite here

| | |
|---|---|
| **ADR-0002** | The pricing engine is pure. No I/O, no clock, no database, no floats. Rates are *given* to it, never looked up by it — that is what makes a quote reproducible. |
| **ADR-0008** | Alembic is the only schema mechanism. One head. `alembic check` must agree with the ORM, and a migration that does not `downgrade` cleanly is not finished. |
| **ADR-0012** | Errors are machine-readable *codes* with structured details, never localized prose. The client renders them. |
| **ADR-0020** | A rate snapshot is persisted per order, so changing a rate never reprices work already quoted. |
| **ADR-0021** | Tests run on real PostgreSQL. There is no SQLite fallback; adding one is not a fix, it is how three features stopped being covered last time. |

## Layering

Enforced by `lint-imports`, and the contracts are the design rather than a style
preference:

- `core` imports no context. It is the foundation and knows nothing about the farm.
- A context imports another context's public `__init__` **only** — never its
  `models` or `service`. `check_context_isolation.py` catches what import-linter
  cannot express.
- `api` and `workers` are siblings and may not import each other. Shared code goes
  to `core`; that is why `LIVE_PATTERNS` lives in `core/relay.py`.

## File length

400 lines, hard, and it is a gate rather than a guideline. Split at a real seam —
by responsibility. `workers/runner.py` split into `runtime.py` (set up once) and
`passes.py` (one unit of work) when it grew past the limit; that is the shape to
copy, not a cut where the counter happened to trip.

## Traps

**Never run two test sessions at once.** They share `printorian_test` and truncate
each other's tables. The result is failures that do not reproduce, and it has
already sent one investigation down a blind alley.

**Alembic runs against the dev database, not the test one.** `alembic upgrade head`
touches `printorian`. Check `alembic current` before a `downgrade`.

## Do not "fix" these

- **`customer_storage_quota_bytes` is displayed, not enforced.** Refusing a quote
  mid-configuration is the wrong behaviour; the setting's comment says so.
- **Rate limiting and the sign-in lockout are in-process.** Correct while the
  deployment is one API process (ADR-0003). The consequences — counters reset on
  restart, a second replica gets its own allowance — are recorded in
  `docs/DATABASE-REVIEW.md` §9 alongside the other accepted trade-offs.
- **The retention clamp in `workers/maintenance.py`.** The telemetry drop cutoff is
  `min(now − retention, watermark)`, and the second term is the only thing standing
  between a stalled summariser and irreversibly deleted history. If you touch
  retention or rollups, preserve it.
