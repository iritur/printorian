# ADR-0018 — High-volume tables are time-partitioned, with explicit retention

**Status:** Accepted · 2026-08-11

## Context
Two tables will hold roughly 99% of the rows in this database, and every other
table together will not approach either.

At the default five-second telemetry poll, fifty printers produce about **315
million** telemetry rows a year. `assignment_records` is written for every ready
job on every planning pass, so a job that waits two hours across 30-second ticks
and event-driven replans accrues hundreds of rows — order **17 million a year**, at
2–5 KB each.

This is the fact behind the question "should the database be split?". The answer is
no — ADR-0001 stands, and splitting to solve a volume problem would buy a mirrored
domain and a sync layer to fix two tables. What is actually needed is for those two
tables to stop behaving like the other nineteen.

Retention on an unpartitioned table means `DELETE FROM ... WHERE created_at < ...`
over tens of millions of rows: hours of runtime, locks held throughout, and bloat
afterwards that only `VACUUM FULL` reclaims. Dropping a partition is a catalogue
operation — constant time, whatever is in it.

Partitioning is also the one schema decision here that cannot be deferred.
Creating a table partitioned costs nothing. Converting a 300-million-row table to a
partitioned one means building a copy, moving the data and swapping them, with
writes stopped for the duration.

## Decision
`telemetry_samples` is declaratively partitioned by month on `created_at`, from its
first row. A `DEFAULT` partition exists as a safety net and is expected to stay
empty.

`contexts.fleet.retention` owns the partition lifecycle: `ensure_partitions` runs
ahead of the data, `drop_partitions_before` runs behind it, and both are driven by
the maintenance worker.

`assignment_records` is not partitioned yet. It is two orders of magnitude smaller
than telemetry and its growth is bounded by planning frequency rather than by the
clock, so it is watched rather than pre-split. When it needs it, this ADR is where
the pattern already is.

## Consequences
* PostgreSQL requires the partition key in every unique constraint, so
  `telemetry_samples` has a composite primary key `(id, created_at)` and does not
  inherit `Entity`.
* Alembic's autogenerate sees partition children as tables the models forgot and
  proposes dropping them. `alembic/env.py` filters anything prefixed
  `telemetry_samples_` out of the comparison.
* Partitions are not self-maintaining. A month with no partition is a **failed
  insert**, so provisioning runs hourly and reports anything that lands in the
  `DEFAULT` partition — telemetry still recorded, but in a partition retention
  cannot drop and queries cannot prune.
* **`telemetry_retention_days` defaults to `0`, meaning no dropping.** Dropping is
  irreversible, and until rollups exist (Slice G) the raw samples are the only copy
  of what the farm measured. Retention is enabled in the same change that starts
  summarising them, not before.
* The escape hatch, if partitioning ever stops being enough, is TimescaleDB on the
  same instance or telemetry alone on a second one. Partitioning is the prerequisite
  for either, so this is the common prefix of every future path rather than a bet.
