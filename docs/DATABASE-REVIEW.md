# Database — architecture, integrity, scalability

The persistence layer of Printorian, as it stands: what is stored, what the
database itself guarantees, how it behaves as the farm grows, and what remains to be
built.

Read alongside [ARCHITECTURE.md](ARCHITECTURE.md) for the system it serves,
[DESIGN-KIT.md](DESIGN-KIT.md) for the screens still waiting on it, and
[RUNBOOK-BACKUP-RESTORE.md](RUNBOOK-BACKUP-RESTORE.md) for operating it.

---

## 1. Shape

One PostgreSQL database (ADR-0001, D1). **22 tables** across seven contexts, built
by nine Alembic migrations on a single linear head.

| Context | Tables |
|---|---|
| `identity` | `users`, `sessions` |
| `inventory` | `material_specs`, `material_lots` |
| `ordering` | `orders`, `order_lines`, `order_events`, `rate_snapshots` |
| `payments` | `payments`, `refunds`, `payment_notifications` |
| `catalog` | `model_assets`, `prepared_plates` |
| `fleet` | `printers`, `ams_slots`, `service_operations`, `telemetry_samples` |
| `production` | `print_jobs`, `job_events`, `assignment_records`, `wait_list_entries`, `estimate_variances` |

`pricing` and `scheduling` own no tables at all. Both are pure functions
(ADR-0002, ARCHITECTURE §6), and import-linter enforces that `pricing` cannot even
import SQLAlchemy. That absence is a load-bearing part of the design, not an
omission: it is what makes a quote reproducible and a planning decision testable
against fixtures.

### 1.1 How the tables relate

```
users ─┬─< sessions
       ├─< orders.customer_id            (SET NULL)
       ├─< order_events.actor_id         (SET NULL)
       └─< prepared_plates.sliced_by     (SET NULL)

rate_snapshots ──< orders.rate_snapshot_id   (RESTRICT)

orders ─┬─< order_lines                  (CASCADE)
        ├─< order_events                 (CASCADE)
        ├─< payments                     (RESTRICT) ─┬─< refunds              (CASCADE)
        │                                            └─< payment_notifications (CASCADE)
        └─< print_jobs                   (CASCADE) ─┬─< job_events            (CASCADE)
                                                    ├─< assignment_records    (CASCADE)
                                                    ├─< wait_list_entries     (CASCADE)
                                                    └─< estimate_variances    (CASCADE)

printers ─┬─< ams_slots                  (CASCADE)
          ├─< service_operations         (CASCADE)
          ├─< material_lots.printer_id   (SET NULL)
          └─< print_jobs.printer_id      (SET NULL)

material_specs ──< material_lots         (CASCADE) ──< ams_slots.lot_id  (SET NULL)

model_assets ─┬─< order_lines.model_asset_id     (RESTRICT)
              ├─< print_jobs.model_asset_id      (RESTRICT)
              └─< prepared_plates.model_asset_id (SET NULL)

prepared_plates ──< print_jobs.prepared_plate_id  (SET NULL)

telemetry_samples          — partitioned by month, no inbound references
```

Two aggregates deserve attention because they carry the ideas the whole system
rests on.

**`model_assets`** is the geometry, and the reason the rest of the chain works.
An upload is content-addressed by SHA-256: the bytes go to the object store under
that digest, the row holds the reference and the mesh analysis, and re-uploading a
file the farm already has costs one hash and no disk. That digest is what
`plate_key` is built from, so two customers uploading the same part — under any
names — share one prepared plate. A filename could never do that job:
`OrderLine.model_name` is a label, and two different parts called `part.stl` are
otherwise indistinguishable.

**`prepared_plates`** is ADR-0006 made physical. `plate_key` is content-addressed
over `(model, scale, material, printer profile, layout)` and carries a unique
constraint, so two engineers slicing the same configuration collapse to one row
rather than racing. That constraint is what stops human-gated slicing from scaling
linearly with orders.

**`rate_snapshots`** is what makes ADR-0002's reproducibility claim true. The order
stores the pinned `Breakdown`, the `engine_version`, and a `RESTRICT` foreign key to
the immutable rate bundle the quote was built from. The breakdown alone can be
*displayed*; only the snapshot lets the engine be re-run and the figure
*recomputed*. The primary key is the bundle's own content hash, so identical rates
are one row by construction.

**`assignment_records`** answers "why did job #4127 go to P1S-03?", including for
jobs that were *not* assigned — every candidate, its rejection reasons and its score
components, stored as one document. A schema that could only explain the winners
could not answer "why was my job skipped", which is the question a customer actually
asks.

---

## 2. Foundations

| | Why |
|---|---|
| **Alembic is the only schema mechanism** (ADR-0008) | One head, linear. `tests/test_migrations.py` asserts `upgrade head` from empty, `downgrade base`, and `alembic check` for model/migration drift — against real PostgreSQL, on every CI run. The third assertion is the one that makes the migrations authoritative rather than merely present. |
| **UUIDv7 primary keys** (`core/ids.py`) | The leading 48 bits are a millisecond timestamp, so keys sort by creation time. That gives B-tree insert locality on a telemetry-heavy write path, makes "recent rows" range-scannable, and lets pagination seek on the primary key with no secondary sort column. |
| **`MetaData` naming convention** | Every constraint and index has a derived, predictable name. This is what makes autogenerated migrations reviewable and `alembic check` trustworthy. |
| **`UtcDateTime`** | Timezone-awareness enforced at the database boundary — a naive datetime raises rather than being stored ambiguously. |
| **`Numeric` for money, mass and minutes** | No float anywhere near a price (D12). `Money` carries its currency explicitly and rounding is centralised. |
| **`JsonB`** (ADR-0017) | One declaration, `postgresql.JSONB`. Documents are stored binary — indexable, containment-queryable, parsed once — rather than as text reparsed on every access. It was a `with_variant` while the suite ran on SQLite; ADR-0021 removed the second behaviour along with the second engine. |
| **Blobs out of the database** | Plates and models are referenced by `storage_path` and hash, never stored inline. Keeps the dump small enough to restore quickly, and the object store swappable. |
| **`printorian/models.py`** | One list of every table-defining module, imported by both Alembic and the worker. SQLAlchemy resolves foreign keys by name at flush time, so a process that imported only its own context would work until the first cross-boundary write and then fail deep inside the unit of work. |
| **One database everywhere, including tests** | ADR-0021: the whole suite runs on real PostgreSQL, isolated by `TRUNCATE` between tests. There is no fast-but-different dialect, because a suite on another engine was quietly excusing partitioned telemetry, the order-number sequence and foreign keys from coverage altogether. |

---

## 3. What the database itself guarantees

Application code is not the only writer a database ever has. These are enforced in
the schema, so a service bug, a manual `UPDATE` during an incident, or a future
second writer cannot get past them.

### Referential integrity

Twenty-eight foreign keys, each with a deliberate delete rule:

- **`CASCADE`** where the child has no meaning without its parent — order lines,
  events, refunds, job history.
- **`SET NULL`** where the child outlives the reference. Removing a member of staff
  must not delete the record of what they did; decommissioning a printer must not
  destroy the jobs it ran.
- **`RESTRICT`** on `payments.order_id`, `orders.rate_snapshot_id`, and the two
  references to `model_assets` — neither an order with money against it, nor the
  rates a price depends on, nor geometry a job still has to print may be deleted
  out from under it. The last of those is load-bearing: it is the *whole* of what
  stops model retention collecting a mesh an open order needs, which is why the
  sweep never has to ask `ordering` anything.

The **material ↔ AMS slot ↔ printer triangle** is fully constrained, which matters
more than it looks: it is the exact data the scheduler's hard eligibility filter
runs on, so a dangling reference there would present the planner with filament that
does not exist.

### Uniqueness

| Constraint | What it prevents |
|---|---|
| `uq_payments_idempotency_key` | A retried checkout charging twice |
| `uq_payment_notifications_provider_event_key` | A redelivered webhook settling twice |
| `uq_model_assets_sha256` | The same geometry stored twice, and two prepared plates for what is really one model |
| `uq_prepared_plates_key` | Two cached plates for one configuration, and a race over which later orders hit |
| `uq_ams_slots_printer_id_unit_index` | A duplicated slot row presenting the scheduler with capacity that is not there |
| `uq_{order,job}_events_*_sequence`, `uq_refunds_payment_id_sequence` | Ambiguous ordering in the append-only histories. `sequence` is the only dependable ordering — UUIDv7 orders only to the millisecond, and two events in one millisecond are not hypothetical on an append-only history written in a loop — so the constraint is what makes it dependable rather than merely intended |
| `uq_orders_number`, `uq_users_email`, `uq_printers_name` | The obvious ones |

### Domain invariants

Forty-eight CHECK constraints. Money and mass are non-negative; `refunded_amount <=
amount`; `remaining_grams <= initial_grams`; quantities and scales are positive;
percentages sit in `[0, 100]`; a job cannot finish before it started.

`refunded_amount <= amount` is the one that most earns its keep — without it, a bug
in the refund path or one hand-written `UPDATE` during an incident can return more
money than was ever collected, and nothing objects.

### Enforced by CI

`tests/test_schema_contracts.py` holds five rules by inspecting the metadata, in the
same spirit as the import contracts:

1. every `*_id` column has a foreign key **or** a written reason in an explicit
   exemption list;
2. every `sequence` column is unique within its parent;
3. every foreign key has an index leading on it — PostgreSQL does not create one,
   so without it every read through the key, and every cascading or `SET NULL`
   delete, is a sequential scan;
4. no column uses plain `JSON` instead of `JsonB`;
5. no exemption in the list is stale.

The point of rule 1 is not the foreign key. It is that *the choice has to be made
and written down* rather than made by omission.

---

## 4. Concurrency

**Planning is single-flight.** ARCHITECTURE §6 requires an event-driven re-plan as
well as the 30-second tick, so passes overlap by design. `claim_ready_jobs` takes
`pg_advisory_xact_lock` and selects with `FOR UPDATE SKIP LOCKED`, bounded to 500
jobs. Two passes therefore cannot assign one job to two machines — two plates on two
beds for one order. The lock is transaction-scoped, so a crashed pass releases it
rather than leaving the farm unable to plan.

**Order numbers come from a PostgreSQL sequence.** Concurrent checkouts never
collide, and issuing number 50,000 costs what issuing number 1 costs. Gaps appear
when a checkout rolls back, which is the accepted trade: a gap is cosmetic, a
collision is a failed payment.

**Rate snapshots upsert.** `ON CONFLICT DO NOTHING` on the content hash — two orders
priced from identical rates race to insert the same row and the loser has nothing to
correct.

**Sequences within a parent** are `MAX(sequence) + 1`, answered from the unique index
rather than by counting rows, with the constraint turning a lost race into a
retryable error instead of a silently duplicated position.

---

## 5. Query shape and performance

Thirty-seven indexes, plus the thirteen unique constraints that build their own —
each traceable to a query the system actually issues, and none duplicating another.

**Partial indexes on the two hot status predicates.** The planner reads
`status = 'ready'` on every tick and every triggering event;
`ix_print_jobs_ready_priority` covers only those rows, so it stays the size of the
queue rather than of all production history and remains cached permanently. The
order desk gets the same treatment through `ix_orders_open_created_at`, whose
predicate is derived from `OPEN_STATUSES` so the index and the application cannot
disagree about what "open" means.

**Keyset pagination** (`core/pagination.py`) on the orders table, seeking on the
UUIDv7 primary key. `OFFSET 10000` makes PostgreSQL walk and discard ten thousand
rows before returning anything — the last page of a long list is the slowest, which
is backwards — and silently skips or repeats rows when something is inserted
mid-scroll. A keyset cursor is an index seek: page one thousand costs what page one
costs. Page size is capped server-side.

**Status counts are a `GROUP BY`, not a tally of the page.** The chips above a table
describe the table; counting the returned rows would make them say "12 printing"
when they meant "12 printing on this page".

**Eager loading throughout.** `selectinload` on every relationship read in
`ordering`, `fleet`, `inventory`, `payments` and `production` — no N+1 anywhere.

**The pinned price is never recomputed.** An order carries its breakdown verbatim,
so the desk renders historical quotes without touching the pricing engine.

**Connection pooling and server-side guards** are explicit (`core/db.py`): pool size
and overflow sized for API handlers and worker sweeps sharing one process,
`pool_recycle` ahead of a firewall silently dropping an idle connection, and
`statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout` sent per
connection so they travel with the application onto any box it is restored to.

---

## 6. Scaling with the farm

Take the growth curve as 6 printers today → 20 in two years → 50 at the practical
ceiling of one on-prem box, and orders from ~30/day → ~200/day.

| Table | Rows / year at the top end | Notes |
|---|---|---|
| **`telemetry_samples`** | **~315M** | 5-second poll × 50 printers |
| **`assignment_records`** | **~17M** | Every ready job on every planning pass; ~2–5 KB each → 50–80 GB/year |
| `job_events`, `order_events` | ~700k each | |
| `orders` + `order_lines` | ~150k | |
| `payments` + notifications | ~150k | |
| everything else | thousands | |

**Two tables are roughly 99% of the data. The other nineteen stay in
"one PostgreSQL, no thought required" territory for a decade.**

### The database is not split, and should not be

ADR-0001 holds. Cross-context joins are rare by design, the contexts are already
isolated at the code level, and a distributed transaction across `orders` and
`payments` would be a strict downgrade — it would reintroduce exactly the failure
mode ADR-0001 exists to prevent.

What the two large tables get instead is **time partitioning** (ADR-0018).
`telemetry_samples` is partitioned by month from its first row, because that
decision is the one that cannot be deferred: creating a table partitioned costs
nothing, while converting a 300-million-row table means building a copy, moving the
data and swapping them with writes stopped.

Retention then becomes `DROP TABLE telemetry_samples_2026_03` — a catalogue
operation, constant time whatever the row count — rather than a `DELETE` that would
run for hours, hold locks throughout, and leave bloat only `VACUUM FULL` reclaims.

Partitions are not self-maintaining, and a month with no partition is a *failed
insert*. `contexts/fleet/retention.py` provisions months ahead of the data and drops
whole months behind it; the maintenance worker runs it hourly. A `DEFAULT` partition
catches anything that slips through, and the sweep reports it — telemetry still
recorded, but somewhere retention cannot drop and queries cannot prune.

If partitioning ever stops being enough, the escape hatch is TimescaleDB on the same
instance, or telemetry alone on a second one. Partitioning is the prerequisite for
either, so it is the common prefix of every future path rather than a bet on one.

### Speed at scale

No read replica is needed, and adding one would introduce an operational failure
mode to a single-site deployment for no gain. If the dashboard grows heavy, rollup
tables are the answer. Beyond that: tune the box (`shared_buffers` ≈ 25% RAM,
`effective_cache_size` ≈ 60–75%, `work_mem` against expected concurrency,
`max_connections` above what the pool can open) — figures and reasoning are in the
runbook.

---

## 7. Backup and recovery

Four artifacts and one job that proves they work (ADR-0019, procedure in the
[runbook](RUNBOOK-BACKUP-RESTORE.md)).

- **WAL archiving plus base backups** — recovery point of roughly one minute rather
  than one day. `archive_mode` is on in `docker-compose.yml` rather than left to the
  operator, because it cannot be enabled without a restart.
- **A nightly `pg_dump -Fc`**, verified with `pg_restore --list` as it is written. A
  logical dump survives a class of disaster a physical one does not — block
  corruption, a bad major-version upgrade — and restores anywhere.
- **An encrypted off-site copy.** A single on-prem box whose only backups are on
  that box is one fire from total loss.
- **A scheduled restore drill** (`scripts/restore_drill.py`): restore last night's
  dump into a scratch database, run `alembic check` against it, and assert the
  tables a recovery needs first are not empty.

That last assertion is the one that earns its place. A backup script pointed at the
wrong database produces a perfectly valid, perfectly restorable, **empty** dump every
night, and every other check passes. It is the failure most likely to go unnoticed
for months and the worst one to discover mid-incident.

Two constraints that are easy to miss:

- **Backups are secret material.** The dump carries every `password_hash` and every
  `printers.access_code_encrypted`. `PRINTORIAN_SECRET_KEY` is escrowed *separately*
  — restoring without it returns every order and a fleet nobody can drive.
- **Blobs and the database must be consistent.** Write the blob before the row, and
  snapshot blobs before the database; together those make it impossible for a
  restored database to name a blob the restored store lacks. Content-addressing makes
  the sync incremental and a partial sync detectable.

---

## 8. What the design kit and roadmap still need

[DESIGN-KIT.md](DESIGN-KIT.md) §2 enumerates the missing backend screen by screen.
Expressed as schema:

| Slice / phase | Tables |
|---|---|
| **D — settings** | `settings`, `settings_audit` |
| **E — recovery, sessions, account** | `password_reset_codes`, `addresses`, `payment_methods`, `notification_preferences` |
| **F — catalogue** | `catalog_categories`, `catalog_tags`, `model_ratings`, with measured time and price derived from completed jobs |
| **G — dashboard** | `metric_rollups` over `telemetry_samples` |
| **H — post-production and procurement** | `postproduction_stages`, `postproduction_tasks`, `qc_records`, `packages`, `shipments`, `suppliers`, `purchase_orders`, `purchase_order_lines`, `maintenance_schedules` |
| **I — journal** | `posts`, `post_categories` |

Roughly **22 → ~45 tables by Phase 7**. That is a normal trajectory and the current
design absorbs all of it without restructuring: every one of those is an ordinary
additive table, and the three decisions that would have been expensive to defer —
JSONB, telemetry partitioning, and content-addressed model storage — are already
made.

**Slice A's storage half is built.** Uploads are stored on disk under their SHA-256
behind `core.storage`, `model_assets` holds the reference and the mesh analysis, and
`plate_key` is keyed on that digest — so the chain payment → prep → slice → dispatch
now carries real bytes end to end. The prep queue serves the model for download, the
engineer's plate is parsed and stored, and the dispatcher reads it back and refuses
rather than sending an empty file. What Slice A still owes is presentation: the
catalogue screen and the customer's own-models list are frontend work on data that
now exists.

One documented divergence worth an explicit ruling: ARCHITECTURE §4.2 shows
`SlaCommitment` and `PriceCredit` as separate aggregates, while the schema inlines
them as `orders.promised_at` / `decay_policy` / `sla_credit`. The inlining is
simpler and defensible — but the document and the schema currently disagree, and the
point is to choose rather than leave two answers standing.

---

## 9. Known gaps and accepted trade-offs

**Enum columns carry no database-level CHECK.** SQLAlchemy names a generated enum
constraint after the *enum type*, not the column, so a table with two columns of the
same enum produces two identically-named constraints and PostgreSQL refuses to build
the schema. `order_events` has exactly that shape — `from_status` and `to_status` are
both `OrderStatus`. Avoiding it means a hand-picked unique name at every call site, a
rule that holds until somebody adds the thirteenth enum column and forgets. Values
are validated on write by the type and at the edge by Pydantic, and nothing but
application code writes these columns, so the gap is narrow. Recorded in
`core.db.enum_column` rather than left to be rediscovered.

**`assignment_records` is not partitioned.** Two orders of magnitude smaller than
telemetry, and its growth is bounded by planning frequency rather than by the clock.
It is indexed and watched; ADR-0018 already carries the pattern for when it needs
splitting.

**`chosen_printer_id` and `telemetry_samples.printer_id` are typed UUIDs with no
foreign key**, and are on the CI gate's exemption list with their reasons. Both are
immutable history. For an audit record, `SET NULL` would erase the answer to the only
question the table exists to answer, and `RESTRICT` would make retiring a printer
impossible; for partitioned telemetry, a cascading delete through hundreds of millions
of rows is an outage rather than a cleanup.

**Pagination covers the orders table only.** `fleet.table()` and the materials
listing are still unbounded — both are bounded in practice by the size of the farm
rather than by history, so they can wait for the screens that consume them.

**Telemetry retention is on** (`telemetry_retention_days = 90`), enabled in the same
change that started summarising — which was the condition. Dropping a partition is
irreversible, so the guard is not the call order but a clamp: the cutoff is
`min(now − retention, watermark)`, where the watermark is the hour `metric_rollups`
has actually reached. Summarising that stalls stops the dropping with it, and a farm
that has never summarised an hour drops nothing.

**Off-site backup sync has a recipe but no committed job** — the destination and its
credentials are farm-specific.

**Rate limits and the sign-in lockout are in-process** (`core/ratelimit.py`). The
deployment is one API process (ADR-0003), so a Redis-backed counter would buy
correctness across replicas that do not exist and put a dependency in the path that
has to work when Redis is down. Two consequences follow and are worth knowing
rather than discovering: the counters reset on restart, and a second API replica
would get its own allowance rather than sharing one. The fix, when that is real, is
the Redis this process already talks to for the event relay.

**`customer_storage_quota_bytes` is displayed and not enforced.** The account screen
measures against it; no upload is refused by it. That is the deliberate choice
recorded beside the setting — a customer who has hit the ceiling should be told
which of their files to remove, not have a quote refused mid-configuration — and it
means uploaded geometry is bounded by `model_retention_days` collecting what nothing
has used, not by the quota. Making it a rule is a change to the configurator's
flow, which is a product decision rather than a missing check.

---

## 10. Work forward

Ordered by whether deferring costs anything.

### With the slice that needs it

| # | Task |
|---|---|
| DB-1 | `settings` + `settings_audit`. Reads fall back to the code default so an empty table behaves exactly as today; `RateSnapshot` resolution stays at the edge so ADR-0002 purity holds. **The largest remaining gap** — see [DESIGN-KIT.md](DESIGN-KIT.md) §2.1 for the ~100 parameters. |
| DB-5 | Procurement, and the warehouse and logistics entities behind it: `PurchaseOrder`, `Supplier`, storage cells, a movement ledger, `Shipment`. Post-production and QC, which were part of this line, are done. |
| DB-7 | Order → job creation, carrying `model_asset_id` and `model_hash` from the paid line onto the job. The columns and the constraints are in place; nothing yet writes them outside tests. |
| — | **Service tickets**, with steps, assignee and consequence, plus a failure record for MTTR and reliability. `ServiceOperation` exists; the ticket does not. |

Done since this review was written: **DB-2** (`metric_rollups`, and retention
enabled behind the clamp above), **DB-3** (`addresses`, `notification_prefs`),
**DB-4** (`catalog_models` and its material rows), **DB-6** (`journal_posts`,
`journal_subscribers`).

### When the numbers say so

| # | Task | Trigger |
|---|---|---|
| DB-8 | Partition `assignment_records` | table exceeds ~10M rows or 20 GB |
| DB-9 | Paginate the fleet and materials listings | either exceeds a few hundred rows |
| DB-10 | Evaluate TimescaleDB, or telemetry on its own instance | partitioned telemetry stops being enough |
| DB-11 | Commit the off-site sync job | a destination is chosen |
| DB-12 | Enforce foreign keys across the whole fast suite | the 66 tests that build against a fabricated parent id are tidied |

---

## 11. Summary

The schema is sound and the expensive decisions have been made. Money is `Decimal`
and constrained; idempotency, the plate cache and event ordering are guaranteed by
constraints rather than by convention; planning is single-flight; the two tables that
will dominate the database are understood and one of them is already partitioned;
backups have a one-minute recovery point and a drill that proves they restore.

Geometry is stored on disk under its own hash, and the database holds the reference,
the measurements and the digest the plate cache is keyed on. The chain from a paid
order to a printer starting a job carries real bytes end to end, and a dispatcher
that cannot read a plate refuses rather than sending an empty file.

The database is not the constraint on this project, and no longer hides one. What is
left is the work each remaining slice brings with it.
