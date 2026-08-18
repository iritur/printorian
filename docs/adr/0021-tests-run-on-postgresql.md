# ADR-0021 — The test suite runs on PostgreSQL, with no SQLite fallback

**Status:** Accepted · 2026-08-17
**Amends:** [ADR-0001](0001-one-backend-one-database.md), whose "one database" was
true of production and not of the suite that tested it.

## Context

Until now the logic tests ran on file-backed SQLite and only `test_migrations.py`
touched PostgreSQL. The stated trade was speed: the suite needed no Docker and a
developer could run it anywhere.

`tests/conftest.py` carried the escape clause in its own docstring — *"If a model
ever needs a Postgres-only type, the SQLite tests fail loudly and that is the
signal to move this fixture onto Postgres."* That signal had already fired several
times without being read as one.

**What the second database was costing.**

*Production code forked on dialect in two places.* `OrderingService._pin_rates`
chose between `postgresql_insert` and `sqlite_insert`, and `core.db.create_engine`
skipped all pool sizing when the URL began `sqlite`. Neither branch existed for a
production reason; both were there so the tests could run somewhere else.

*Three features had no coverage at all.*

| Feature | Under SQLite |
|---|---|
| `telemetry_samples` partitioning (ADR-0018) | Built as an ordinary table. Partition creation and retention were never executed |
| `ORDER_NUMBER_SEQUENCE` | SQLite reports no sequence support, so the DDL was skipped and `_next_number` took a counting fallback. The concurrency fix protecting the moment a customer pays was never exercised |
| Foreign keys | **Not enforced.** `enforce_foreign_keys` existed to switch them on per test, and its docstring recorded that switching them on globally failed 66 tests |

*`JsonB` was a `with_variant`* — JSONB in production, plain JSON in tests. ADR-0017
exists precisely because that difference matters.

The catalogue work (Aug 2026) added two more instances within a single afternoon.
SQLite's `lower()` is ASCII-only, so a Cyrillic search test failed where PostgreSQL
would have passed — in a catalogue whose entire vocabulary is Russian. And JSONB
containment being unavailable pushed the material facet onto a join table. Both
resulting designs are better ones, but that is luck rather than method: the same
divergence just as easily produces a green suite over a broken feature.

## Decision

**Every test runs against real PostgreSQL. There is no fallback.**

- The suite owns a `printorian_test` database, created on demand
- The schema is `create_all` plus a default partition for `telemetry_samples`;
  isolation between tests is `TRUNCATE ... RESTART IDENTITY CASCADE`, which leaves
  `commit()` meaning exactly what it means in production
- `order_number_seq` is reset explicitly — `RESTART IDENTITY` only resets sequences
  *owned by* a truncated column, and this one stands alone
- Absent PostgreSQL is a **failure, not a skip**. A suite that skips itself when
  the database is missing can report success having tested nothing, which is the
  failure mode this ADR removes
- Fixtures that open their own connection depend on `clean_database`. Under SQLite
  they were isolated by accident, because `settings` handed out a fresh `tmp_path`
  file per test; one shared database has no such accident

## Consequences

**Removed:** both production dialect branches, the `JsonB` variant, the
`enforce_foreign_keys` fixture. This ADR deletes code rather than adding it.

**Found immediately.** The first full run was 66 failed and 68 errors against 631
passed — 66 being exactly the number the old docstring predicted. 275 of 348
violations were `print_jobs.order_id` referencing orders that had never existed:
a state production cannot reach, since a job only comes into being from an order.
Those tests had been asserting about an impossible world. Repairing them meant
giving jobs real orders, real printers, real plates, real users and real lots.

**Cost.** Docker is now required to run the suite, and it takes ~5½ minutes rather
than ~3½. Per-test engines use `NullPool`: each test gets its own event loop, and a
pooled connection outliving its loop is finalised by the garbage collector instead
of closed, which asyncpg reports as an unraisable exception in whichever unrelated
test happens to trigger collection.

**One exemption.** `tests/api/test_events_ws.py` drives an async app from a
synchronous `TestClient` and tolerates `PytestUnraisableExceptionWarning`, forcing
a `gc.collect()` in its own teardown so the finalisation lands inside the module
that tolerates it. Scoped to that file; warnings stay fatal everywhere else.

## Alternatives considered

**Keep SQLite and accept the gaps.** Rejected: the gaps are the order-number
sequence, telemetry partitioning and foreign keys — the concurrency fix at the
moment of payment, the table that decides whether the database scales, and the
constraints the scheduler's eligibility filter depends on.

**Testcontainers.** A reasonable alternative that would remove the "start Docker
first" step. Rejected for now because `docker-compose.yml` already defines the
service, and adding a dependency to reach the same daemon buys little. Reopenable.

**Keep both, run SQLite by default and PostgreSQL in CI.** Rejected as the worst
of the options: it keeps every dialect branch, and moves the discovery of any
divergence from the developer's machine to CI, where it is more expensive.
