---
name: printorian-alembic-traps
description: "Alembic in Printorian runs against the DEV database (printorian, not printorian_test); autogenerate misses USING clauses, partitioning and order_number_seq; enum widening needs core.db.enum_column with NOT VALID/VALIDATE; a migration without a clean downgrade is not finished (ADR-0008)."
metadata:
  node_type: memory
  type: project
---

**Why:** The gap between "alembic autogenerate produced something" and "the
migration is correct on PostgreSQL" is where schema bugs live — and the dev
DB is the one Alembic touches, so a mistaken downgrade destroys real local
state.

**How to apply:** Check `alembic current` before any `downgrade`. Treat
autogenerate output as a draft, then hand-fix: `USING` clauses for type
changes (pattern set by `0005_schema_hardening` for JsonB), partitioned
tables (`0018`), the `order_number_seq` sequence, and `op.f()`-bound
constraint names from the MetaData naming convention. Enum widening goes
through `core.db.enum_column` CHECK constraints with `NOT VALID` then
`VALIDATE` for large tables. Finished means: upgrade → downgrade base →
upgrade → `alembic check` → `tests/test_migrations.py` green.

Evidence: backend/CLAUDE.md ("Traps", "The ADRs that bite here"),
docs/DEVELOPMENT.md (Database section), docs/adr/0008-alembic-only.md.

Disproven shortcut: "autogenerate + fix later in review" — the review cannot
see the `USING` clause autogenerate never wrote.

Related: [[printorian-pg-only-tests]].
