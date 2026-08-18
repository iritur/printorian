# ADR-0017 — JSONB is the JSON storage type

**Status:** Accepted · 2026-08-11

## Context
Thirteen columns across nine tables stored documents with `sa.JSON`, which on
PostgreSQL is the `json` type: the document is kept as text and reparsed on every
access, cannot be GIN-indexed, has no containment operator (`@>`, `?`), does not
deduplicate keys, and occupies more disk than the binary form.

Two of the thirteen are on the fastest-growing tables in the schema.
`assignment_records.candidates` in particular is a multi-kilobyte document written
for every job on every planning pass — the largest single consumer of storage in
the database within a year.

The cost of the fix is not constant. `ALTER TABLE ... ALTER COLUMN ... TYPE jsonb`
rewrites the whole table under an `ACCESS EXCLUSIVE` lock. Today that is
milliseconds. At seventeen million rows it is a maintenance window.

## Decision
Every JSON column uses `core.db.JsonB`, a dialect variant: `JSONB` on PostgreSQL,
plain `JSON` on SQLite.

The variant rather than a bare `JSONB` because the fast test suite runs on SQLite,
which has no such type. The Python-side value is a `dict` or `list` either way, so
nothing above the column notices which dialect it is on.

## Consequences
* Migration `0005_schema_hardening` converts all thirteen with an explicit `USING`
  clause — PostgreSQL will not cast `json` to `jsonb` implicitly, so the clause is
  required rather than decorative.
* Documents become queryable: `price_breakdown @> '{"engine_version": "1.0.0"}'`
  and friends are available for the analytics work in phase 6 without a schema
  change.
* Key order inside a document is not preserved, and duplicate keys collapse to the
  last. Neither matters here: every one of these columns is written from a Python
  `dict`, which has neither property to lose.
