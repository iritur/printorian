# ADR-0008 — Alembic is the only schema mechanism

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
Schema mechanisms multiply when one of them is inconvenient once. A migration tool plus a
hand-written "ensure column exists" patcher, added for a single additive change, ends with
contributors instructed to update both — and with no artefact that authoritatively answers
"what is the schema".

## Decision
Alembic, one head, always. No hand-written schema patcher will be added, in any
circumstance, including "just this once for an additive column".

## Consequences
* CI asserts `alembic upgrade head` from empty, `downgrade base`, `alembic check` for
  model/migration drift, and exactly one head.
* Downgrades are tested - an untested downgrade is not a rollback plan.
