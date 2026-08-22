"""Turning raw telemetry into hours, so the raw telemetry can be thrown away.

Sibling of :mod:`printorian.contexts.fleet.retention`, and driven by the same
maintenance pass, because the two are one arrangement: retention may only drop a
partition whose hours this module has already summarised. Nothing else read
`telemetry_samples` before this existed, which is exactly why
``telemetry_retention_days`` was zero.

**The attribution rule.** Each sample owns the interval from its own `created_at`
to the next sample's, capped at a *gap ceiling* of
``telemetry_poll_seconds × rollup_gap_intervals`` and clipped to the hour it falls
in. A bucket also carries in the last sample at or before its start, so a healthy
hour sums to 3600 seconds *exactly* rather than to the 3595-ish you get by
ignoring the carry-in — an equality worth testing and alerting on.

**Seconds past the gap ceiling are attributed to no state at all.** They are
counted in nothing, and `observed_seconds` — the sum of the eight state columns —
falls short of 3600 by exactly that much. This is ADR-0007 applied to the summary:
an hour nobody polled must not become an idle hour. Every percentage the dashboard
derives divides by `observed_seconds` for that reason, not by 3600.

**Bucketed on `created_at`, not `observed_at`.** `created_at` is server-generated,
monotone per writer, and is the partition key, so the scan prunes. `observed_at` is
the machine's own clock — skewed, repeatable across a reconnect, and quite capable
of going backwards — and differencing it yields negative intervals. The cost is
real and worth naming: **this measures when the farm recorded a state, not when the
machine entered it**, the two differing by the poll's round trip. That is the right
trade for occupancy and the wrong one for phase 6's per-machine timing, which is
why `observed_at` stays on the raw sample. Do not re-derive P&L from this table.

**Aggregated in SQL, which inverts what `production.throughput` does on purpose.**
That module sums durations in Python and says why: its row count is bounded by the
number of jobs. Here it emphatically is not — one hour of one fifty-machine farm is
36,000 sample rows, and a day is 864,000. Pulling those into Python to add them up
would be the same work plus a network round trip per row.

**Idempotent by upsert, not by care.** The statement ends in
``ON CONFLICT (printer_id, bucket_start) DO UPDATE``, so a re-run over a window is
a pure recompute-and-overwrite: a corrected raw sample corrects the row.
``DO NOTHING`` would leave a wrong row wrong. The uniqueness of the natural key is
the guarantee; nothing here has to remember what it already did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.history import STATE_COLUMNS

#: The summary table this module owns.
TABLE = "metric_rollups"

#: The raw table it reads.
SOURCE = "telemetry_samples"


@dataclass(frozen=True, slots=True)
class RollupSweep:
    """What one summarising pass did, for logging and the maintenance outcome."""

    window_start: datetime | None = None
    window_end: datetime | None = None
    rows_written: int = 0

    @property
    def buckets(self) -> int:
        if self.window_start is None or self.window_end is None:
            return 0
        return int((self.window_end - self.window_start).total_seconds() // 3600)


def hour_start(moment: datetime) -> datetime:
    """The UTC hour ``moment`` falls in. UTC, always — see the module docstring.

    Public because `measures.py` cuts its read windows on exactly this boundary.
    Two definitions of "the hour" — one for the writer, one for the reader — is
    how a bucket ends up written under one key and looked up under another.
    """
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


async def latest_bucket(db: AsyncSession) -> datetime | None:
    """The last hour that has been summarised, or ``None`` if none has.

    This is the **retention watermark**. `workers.maintenance` clamps the drop
    cutoff to it, which is what turns "we summarise before we drop" from an
    ordering convention inside one function into an invariant that survives
    :func:`summarise` silently producing nothing. It only ever moves forward, so a
    sweep can never walk back into a window whose partition has already gone.
    """
    value: datetime | None = await db.scalar(text(f"SELECT max(bucket_start) FROM {TABLE}"))
    return value if value is None else value.astimezone(UTC)


async def summarise(
    db: AsyncSession,
    *,
    now: datetime,
    gap_seconds: int,
    max_buckets: int,
    since: datetime | None = None,
) -> RollupSweep:
    """Summarise closed hours from the watermark forward, and say what was written.

    The window runs from the hour after :func:`latest_bucket` to the last *closed*
    hour, capped at ``max_buckets``. Both ends matter:

    * **The current hour is never written.** A partial bucket that nothing rewrites
      is a permanently wrong row, and the live hour is served from raw samples
      anyway.
    * **The cap is not tidiness.** ``db_statement_timeout_ms`` is 30 seconds, and a
      week of catch-up across fifty printers is some six million sample rows in one
      statement. A genuine backfill after a restore is a deliberate script that
      loops with ``since``; the hourly sweep must never widen its own window.

    ``since`` is that deliberate entry point, and the only way to make this walk
    backwards. The sweep does not pass it.
    """
    last_closed = hour_start(now)
    start = hour_start(since) if since is not None else await _next_window_start(db)
    if start is None or start >= last_closed:
        return RollupSweep()

    end = min(start + timedelta(hours=max_buckets), last_closed)
    written = await _summarise_window(db, start=start, end=end, gap_seconds=gap_seconds)

    if written or since is not None or end >= last_closed:
        return RollupSweep(start, end, written)

    # The window held no samples at all — the poller was down, or the farm was.
    # Without this the watermark could never advance past an empty stretch and the
    # sweep would re-scan the same dead hours for ever, never reaching the samples
    # that arrived after them. Skipped hours lose nothing: there was nothing in
    # them to summarise.
    resumed = await _earliest_sample_hour(db, at_or_after=end)
    if resumed is None or resumed >= last_closed:
        return RollupSweep(start, end, 0)

    end = min(resumed + timedelta(hours=max_buckets), last_closed)
    written = await _summarise_window(db, start=resumed, end=end, gap_seconds=gap_seconds)
    return RollupSweep(resumed, end, written)


async def _next_window_start(db: AsyncSession) -> datetime | None:
    watermark = await latest_bucket(db)
    if watermark is not None:
        return watermark + timedelta(hours=1)
    return await _earliest_sample_hour(db, at_or_after=None)


async def _earliest_sample_hour(
    db: AsyncSession, *, at_or_after: datetime | None
) -> datetime | None:
    """The hour of the oldest surviving sample at or after ``at_or_after``.

    Deliberately off the hot path: `printer_id` leads the only index on
    `telemetry_samples`, so a bare ``min(created_at)`` cannot use it and reads the
    partitions the predicate leaves standing. That is affordable exactly twice —
    on the first sweep a farm ever runs, and to step over a stretch with no data —
    and would not be on every sweep.
    """
    where = ""
    params: dict[str, object] = {}
    if at_or_after is not None:
        where = " WHERE created_at >= CAST(:floor AS timestamptz)"
        params["floor"] = at_or_after
    value: datetime | None = await db.scalar(
        text(f"SELECT date_trunc('hour', min(created_at), 'UTC') FROM {SOURCE}{where}"), params
    )
    return value if value is None else value.astimezone(UTC)


async def _summarise_window(
    db: AsyncSession, *, start: datetime, end: datetime, gap_seconds: int
) -> int:
    result = await db.execute(
        text(_STATEMENT),
        {"window_start": start, "window_end": end, "gap_seconds": float(gap_seconds)},
    )
    # `execute` is typed as returning `Result`, which has no row count; a DML
    # statement always produces a `CursorResult`, which does. The cast is the
    # narrowing the async facade does not do for us, not a claim about anything.
    return int(cast("CursorResult[Any]", result).rowcount)


def _duration_aggregates() -> str:
    """One ``SUM ... FILTER`` per state, generated from the enum.

    ``FILTER`` yields null when a state never occurred in the hour, hence the
    ``COALESCE``: an hour a machine spent entirely printing has ``0`` idle seconds,
    not an unknown number of them.
    """
    return ",\n".join(
        f"            ROUND(COALESCE(SUM(seconds) FILTER (WHERE state = '{state.value}'), 0), 2)"
        f" AS {column}"
        for state, column in STATE_COLUMNS.items()
    )


_COLUMNS = tuple(STATE_COLUMNS.values())

#: One statement for the whole window.
#:
#: Two aggregations over the same samples, joined at the end, and they are counted
#: separately for a reason: a span crossing an hour boundary is clipped into *two*
#: buckets and so appears twice, which would double-count `sample_count` and skew
#: the temperature averages if the counts were taken from the span side. Durations
#: come from the spans; everything else comes from the samples themselves.
_STATEMENT = f"""
WITH bounds AS (
    SELECT
        CAST(:window_start AS timestamptz) AS window_start,
        CAST(:window_end AS timestamptz) AS window_end,
        make_interval(secs => CAST(:gap_seconds AS double precision)) AS gap
),
ordered AS (
    -- Extended back by exactly the gap ceiling: that is the carry-in. A sample
    -- older than that speaks for nothing inside the window, because its span is
    -- capped before the window begins.
    SELECT
        s.printer_id,
        s.created_at,
        s.state,
        s.nozzle_temp_c,
        s.bed_temp_c,
        s.error_code,
        s.created_at + b.gap AS gap_end,
        LEAD(s.created_at) OVER machine AS next_at,
        LAG(s.state) OVER machine AS previous_state
    FROM {SOURCE} s
    CROSS JOIN bounds b
    WHERE s.created_at >= b.window_start - b.gap
      AND s.created_at < b.window_end
    WINDOW machine AS (PARTITION BY s.printer_id ORDER BY s.created_at)
),
spans AS (
    SELECT
        printer_id,
        state,
        created_at AS span_start,
        LEAST(COALESCE(next_at, gap_end), gap_end) AS span_end
    FROM ordered
),
clipped AS (
    -- One row per (span, hour it touches). `generate_series` stops one microsecond
    -- short of the span's end, so a span finishing exactly on an hour boundary
    -- does not claim zero seconds of the hour it never entered.
    --
    -- Note for anyone editing these comments: SQLAlchemy's `text()` reads a colon
    -- followed by word characters as a bind parameter, comments included, so a
    -- clock time written here would become a parameter nobody supplies.
    SELECT
        spans.printer_id,
        bucket.start AS bucket_start,
        spans.state,
        EXTRACT(EPOCH FROM (
            LEAST(spans.span_end, bucket.start + interval '1 hour')
            - GREATEST(spans.span_start, bucket.start)
        )) AS seconds
    FROM spans
    CROSS JOIN bounds b
    JOIN LATERAL generate_series(
        GREATEST(date_trunc('hour', spans.span_start, 'UTC'), b.window_start),
        LEAST(spans.span_end, b.window_end) - interval '1 microsecond',
        interval '1 hour'
    ) AS bucket(start) ON TRUE
),
durations AS (
    SELECT
        printer_id,
        bucket_start,
        -- The denominator. Rounded from the whole sum rather than assembled from
        -- the eight, so it can never round its way past the hour.
        ROUND(SUM(seconds), 2) AS observed_seconds,
{_duration_aggregates()}
    FROM clipped
    GROUP BY printer_id, bucket_start
),
counted AS (
    SELECT
        printer_id,
        date_trunc('hour', created_at, 'UTC') AS bucket_start,
        count(*) AS sample_count,
        count(*) FILTER (
            WHERE previous_state IS NOT NULL AND state IS DISTINCT FROM previous_state
        ) AS state_changes,
        count(*) FILTER (WHERE error_code IS NOT NULL) AS error_sample_count,
        ROUND(AVG(nozzle_temp_c), 2) AS nozzle_temp_avg_c,
        MAX(nozzle_temp_c) AS nozzle_temp_max_c,
        ROUND(AVG(bed_temp_c), 2) AS bed_temp_avg_c,
        MAX(bed_temp_c) AS bed_temp_max_c
    FROM ordered
    CROSS JOIN bounds b
    WHERE created_at >= b.window_start
    GROUP BY printer_id, date_trunc('hour', created_at, 'UTC')
),
codes AS (
    SELECT printer_id, bucket_start, jsonb_object_agg(error_code, occurrences) AS error_codes
    FROM (
        SELECT
            printer_id,
            date_trunc('hour', created_at, 'UTC') AS bucket_start,
            error_code,
            count(*) AS occurrences
        FROM ordered
        CROSS JOIN bounds b
        WHERE created_at >= b.window_start AND error_code IS NOT NULL
        GROUP BY printer_id, date_trunc('hour', created_at, 'UTC'), error_code
    ) per_code
    GROUP BY printer_id, bucket_start
)
INSERT INTO {TABLE} (
    printer_id, bucket_start, created_at, updated_at,
    sample_count, observed_seconds, {", ".join(_COLUMNS)},
    state_changes, error_sample_count, error_codes,
    nozzle_temp_avg_c, nozzle_temp_max_c, bed_temp_avg_c, bed_temp_max_c
)
SELECT
    d.printer_id,
    d.bucket_start,
    now(),
    now(),
    -- A bucket can hold spans and no samples of its own: a machine polled two
    -- seconds before the hour turned and then not again until a quarter past
    -- carries a few seconds into the new hour while contributing nothing to its
    -- counts.
    COALESCE(c.sample_count, 0),
    d.observed_seconds,
    {", ".join(f"d.{column}" for column in _COLUMNS)},
    COALESCE(c.state_changes, 0),
    COALESCE(c.error_sample_count, 0),
    COALESCE(k.error_codes, '{{}}'::jsonb),
    c.nozzle_temp_avg_c,
    c.nozzle_temp_max_c,
    c.bed_temp_avg_c,
    c.bed_temp_max_c
FROM durations d
LEFT JOIN counted c ON c.printer_id = d.printer_id AND c.bucket_start = d.bucket_start
LEFT JOIN codes k ON k.printer_id = d.printer_id AND k.bucket_start = d.bucket_start
ON CONFLICT (printer_id, bucket_start) DO UPDATE SET
    updated_at = now(),
    sample_count = EXCLUDED.sample_count,
    observed_seconds = EXCLUDED.observed_seconds,
    {", ".join(f"{column} = EXCLUDED.{column}" for column in _COLUMNS)},
    state_changes = EXCLUDED.state_changes,
    error_sample_count = EXCLUDED.error_sample_count,
    error_codes = EXCLUDED.error_codes,
    nozzle_temp_avg_c = EXCLUDED.nozzle_temp_avg_c,
    nozzle_temp_max_c = EXCLUDED.nozzle_temp_max_c,
    bed_temp_avg_c = EXCLUDED.bed_temp_avg_c,
    bed_temp_max_c = EXCLUDED.bed_temp_max_c
"""


__all__ = ["SOURCE", "TABLE", "RollupSweep", "hour_start", "latest_bucket", "summarise"]
