"""What the fleet was measured doing, as opposed to what it is doing now.

`models.py` holds the machine and its live state — the row a poll overwrites. This
module holds the two tables that are only ever appended to and read back later:
the raw observation, and the hour it rolls up into. The seam is live state versus
measured history, not an arbitrary cut to stay under the length gate;
`contexts.catalog` already keeps its tables in two modules for the same kind of
reason.

The two are deliberately together. `metric_rollups` is a pure function of
`telemetry_samples` over a window, and the attribution rule that connects them —
in :mod:`printorian.contexts.fleet.rollups` — is only correct if both agree on
which clock is being measured. Splitting them across modules would make that
agreement a thing to remember rather than a thing to see.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from printorian.core.db import Base, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId, new_id
from printorian.drivers import PrinterState


class TelemetrySample(Base):
    """One observation of one machine, kept.

    `Printer.last_telemetry` holds the most recent reading and is overwritten every
    poll — five seconds, by default. That is the right shape for "what is this
    machine doing now" and it is the *only* shape the fleet had, which made three
    already-promised things impossible: telemetry retention and rollups (ROADMAP
    phase 3), the dashboard's twelve-hour schedule with a live now-line, and phase
    6's true P&L, which is supposed to draw real electricity from real readings.
    None of that can come from a column holding one row's worth of history.

    **Partitioned by month, from the first row.** This is the table that decides
    whether the database scales with the farm: at a five-second poll, fifty printers
    write about 315 million rows a year, which is two orders of magnitude more than
    everything else in the schema put together. Monthly range partitions cost nothing
    to create now; retrofitting them onto a table that size is a rewrite with
    downtime, and that asymmetry is the whole reason this exists before there is a
    dashboard to read it.

    Retention is then a matter of *detaching* a partition — instantaneous — rather
    than a ``DELETE`` over tens of millions of rows, which would take hours and leave
    the table bloated behind it. See :mod:`printorian.contexts.fleet.retention`.

    Not an `Entity`: PostgreSQL requires the partition key to be part of every unique
    constraint, so the primary key is ``(id, created_at)`` rather than ``id`` alone.
    """

    __tablename__ = "telemetry_samples"
    __table_args__ = (
        # The dashboard's question, and the rollup job's: "this machine, this
        # window". Leading with the printer keeps a single machine's history
        # contiguous; partition pruning handles the time half before the index is
        # even consulted.
        Index("ix_telemetry_samples_printer_id_created_at", "printer_id", "created_at"),
        CheckConstraint(
            "progress_percent IS NULL OR (progress_percent BETWEEN 0 AND 100)",
            name="progress_range",
        ),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[EntityId] = mapped_column(primary_key=True, default=new_id)
    #: Partition key, and half the primary key. Server-defaulted like every other
    #: `created_at` in the schema, so a row cannot land in the wrong partition
    #: because a caller's clock disagreed with the database's.
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, primary_key=True, server_default=func.now(), nullable=False
    )

    #: Deliberately not a foreign key, for the same reason as
    #: `AssignmentRecord.chosen_printer_id`: this is high-volume history that is
    #: dropped a partition at a time, and a cascading delete through hundreds of
    #: millions of rows is an outage rather than a cleanup. Decommissioning a
    #: machine must not rewrite what it was measured doing.
    printer_id: Mapped[EntityId] = mapped_column(nullable=False)

    #: When the *machine* said this, as opposed to when the row was written. The two
    #: differ by the poll's round trip, and phase 6 needs the machine's own timing.
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    state: Mapped[PrinterState] = mapped_column(enum_column(PrinterState), nullable=False)

    job_handle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    progress_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    layer_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    layer_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining_minutes: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    nozzle_temp_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    bed_temp_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)


#: Seconds in one bucket. Named because three constraints and the arithmetic in
#: `rollups.py` all mean the same hour by it.
BUCKET_SECONDS = 3600


class MetricRollup(Base):
    """One printer, one UTC hour, summarised.

    720 raw samples collapse into this row. Fifty printers produce about 438
    thousand rows a year here against 315 million there, which takes the summary
    out of the "two tables are 99% of the data" category entirely: it is never
    partitioned, has no retention, and is meant to be kept for ever. That
    permanence is what makes dropping raw partitions defensible at all — the shape
    the dashboard draws survives the samples it was drawn from.

    **Hourly, and not daily.** `production.throughput.hourly_load` exists because a
    farm idle every night has capacity nobody is selling and a daily total cannot
    show it; the twelve-hour schedule strip has no daily answer either. **And not
    finer.** Fifteen-minute grain is four times the rows for a question no screen
    asks, and the live hour is served from raw samples anyway.

    **One row per printer, never a farm-wide total row.** Summing fifty rows for an
    hour is free. A table mixing member rows with total rows makes every query
    carry an "is this a total" predicate, and one forgotten one double-counts
    silently, with no constraint able to notice.

    **Keyed on UTC, because everything else here is cut in UTC.** A bucket keyed on
    farm-local time is keyed on a boundary that stops existing when `farm_timezone`
    changes or the clocks shift. Presentation converts; storage does not.

    **The natural key is the whole key.** No surrogate `id`, so this is not an
    `Entity`: ``(printer_id, bucket_start)`` already identifies the row, is already
    what the upsert conflicts on, and is what any reader joins by. A surrogate here
    would be a column nothing references — and, because the whole table is written
    by one ``INSERT … SELECT`` that produces a variable number of rows inside the
    database, it would have to be minted in SQL by a different scheme than the
    UUIDv7 `core.ids` gives every other id in the schema. `telemetry_samples`
    above already declines `Entity` for its own reason; this is a second one.

    `updated_at` is kept, and means "when this hour was last summarised" — the one
    audit question a recompute-and-overwrite table can be asked.
    """

    __tablename__ = "metric_rollups"
    __table_args__ = (
        # "the whole farm, this window" — the dashboard's own question. The primary
        # key leads with `printer_id` and so cannot serve it.
        Index("ix_metric_rollups_bucket_start", "bucket_start"),
        CheckConstraint("sample_count >= 0", name="sample_count_non_negative"),
        CheckConstraint("state_changes >= 0", name="state_changes_non_negative"),
        CheckConstraint("error_sample_count >= 0", name="error_sample_count_non_negative"),
        CheckConstraint(
            f"observed_seconds BETWEEN 0 AND {BUCKET_SECONDS}",
            name="observed_seconds_within_the_hour",
        ),
        # Deliberately *not* a constraint that the eight sum to `observed_seconds`.
        # They do, up to the rounding each column takes independently, and encoding
        # "up to rounding" in SQL is a constraint that fails on a Tuesday for
        # arithmetic reasons rather than for anything wrong. `test_rollups.py`
        # asserts the equality on data where it is exact.
        CheckConstraint(
            "offline_seconds >= 0 AND idle_seconds >= 0 AND preparing_seconds >= 0 "
            "AND printing_seconds >= 0 AND paused_seconds >= 0 AND finished_seconds >= 0 "
            "AND error_seconds >= 0 AND maintenance_seconds >= 0",
            name="state_seconds_non_negative",
        ),
    )

    #: Not a foreign key, for the same reason as `TelemetrySample.printer_id`:
    #: retiring a machine must not rewrite or delete what it was measured doing.
    printer_id: Mapped[EntityId] = mapped_column(primary_key=True)
    #: Half-open ``[bucket_start, bucket_start + 1 hour)``, always on a UTC hour.
    bucket_start: Mapped[datetime] = mapped_column(UtcDateTime, primary_key=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), nullable=False
    )
    #: When this hour was last summarised. A re-run rewrites the row, so this moves.
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    #: Raw samples whose `created_at` fell inside the hour. Zero is possible and
    #: meaningful: a machine polled at 09:59:58 and then not again until 10:15 has
    #: a few seconds of carried-in state in the 10:00 bucket and no samples of its
    #: own.
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)

    #: **The denominator of every percentage derived from this row**, and the
    #: reason the eight columns below are not enough on their own. It is the time
    #: in the hour that some sample actually spoke for. An hour where the poller
    #: was down for fifty minutes is otherwise indistinguishable from a full one,
    #: and every figure computed from it is a lie — ADR-0007's rule against
    #: inventing data, applied to the summary. An unpolled hour must not become an
    #: idle hour.
    observed_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)

    # -- where the hour went ------------------------------------------------
    #
    # Eight explicit columns rather than one JSONB state map, because these are
    # exactly what the dashboard aggregates: `SUM(printing_seconds)` over a range
    # is both the honest utilisation figure and the load-map cell. Summing inside
    # JSONB is a lateral join in every one of those queries.
    offline_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)
    idle_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)
    preparing_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)
    printing_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)
    paused_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)
    finished_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)
    #: Time spent *in* `PrinterState.ERROR`. Not the same question as
    #: `error_sample_count` below, and the difference is real: a machine can report
    #: an error code while still printing, which is `error_seconds == 0` with
    #: `error_sample_count > 0`.
    error_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)
    maintenance_seconds: Mapped[Decimal] = mapped_column(Numeric(9, 2), nullable=False)

    #: How many times the reported state differed from the previous observation.
    #: A machine that thrashed between error and printing all hour reads very
    #: differently from one that changed once, and the durations alone cannot say.
    state_changes: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Samples carrying any error code, whatever the state was.
    error_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``{code: occurrences}``. JSONB earns its place here and nowhere else on this
    #: row: the key set is driver-defined and open-ended, and it is read for
    #: display rather than filtered on (ADR-0017).
    error_codes: Mapped[dict[str, int]] = mapped_column(JsonB, nullable=False, default=dict)

    # -- temperatures -------------------------------------------------------
    #
    # Nullable, never zero. `samples.sample_of` refuses to invent a cold bed and
    # this must not undo it; `AVG` ignores nulls natively, so an hour with no
    # readings summarises to null rather than to a fabricated 0 °C.
    #
    # The maxima are the load-bearing pair. An *average* nozzle temperature is only
    # interpretable beside `printing_seconds` — an hour that was idle for fifty
    # minutes averages a cold nozzle and means nothing by it. A printing-only
    # average is a real column to add when phase 6 asks for one; guessing at it now
    # would be a second number nobody could reconcile with this one.
    nozzle_temp_avg_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    nozzle_temp_max_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    bed_temp_avg_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    bed_temp_max_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # No energy column. `Printer.nominal_power_kw` can change, and
    # `printing_seconds / 3600 × rate` at read time keeps one source of truth.
    # Freezing today's nameplate into a historical row is a wrong number waiting to
    # be discovered.


#: The duration column each state summarises into. Built from the enum rather than
#: written out, so a ninth state is a migration and a failing test rather than a
#: silently unattributed hour.
STATE_COLUMNS: dict[PrinterState, str] = {state: f"{state.value}_seconds" for state in PrinterState}


__all__ = ["BUCKET_SECONDS", "STATE_COLUMNS", "MetricRollup", "TelemetrySample"]
