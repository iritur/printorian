---
name: printorian-pg-only-tests
description: "ADR-0021 (2026-08): the test suite runs on real PostgreSQL only — no SQLite fallback; the first PG-only run surfaced 66 failures + 68 errors SQLite had hidden; printorian_test is TRUNCATEd between sessions so two concurrent sessions corrupt each other."
metadata:
  node_type: memory
  type: project
---

**Why:** SQLite passes things PostgreSQL does not (constraint enforcement,
enum/CHECK behavior, `JsonB` typing), so a fallback dialect silently deletes
coverage — three features lost coverage the last time one existed.

**How to apply:** Never add a dialect branch or SQLite skip. Absence of
PostgreSQL on 5433 is a failure to run (`COULD NOT RUN`), not a skip. Run
one pytest session at a time, host-wide.

Evidence (docs/adr/0021-tests-run-on-postgresql.md, docs/DEVELOPMENT.md,
backend/CLAUDE.md):

- First PG-only run (2026-08): 66 failures + 68 errors that SQLite had been
  hiding; full-suite cost measured ~5½ min on the dev box of the time
  (budget ~15 min conservatively today, ~1100 tests).
- Sessions share the `printorian_test` database; each run
  `TRUNCATE ... RESTART IDENTITY CASCADE` and resets `order_number_seq`
  explicitly — an overlapping session is `INVALID` evidence, and one such
  overlap already sent an investigation down a blind alley
  (backend/CLAUDE.md "Traps").
- Single exemption: `tests/api/test_events_ws.py` (per the ADR's
  migration notes).

Related: [[printorian-ports-5433-6380]], [[printorian-alembic-traps]].
