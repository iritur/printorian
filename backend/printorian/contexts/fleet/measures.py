"""Reading `metric_rollups` back out — what the farm was *measured* doing.

Sibling of :mod:`printorian.contexts.fleet.rollups`, which writes the table and
which nothing read until this existed. The pair is deliberate: that module owns
the attribution rule, this one owns the ruler the answers are laid on, and every
rule added here is about what happens where there is no row. The statements
themselves are next door in `measure_sql.py`.

**Absence has exactly one representation: ``None``, and it means "this hour was
never summarised for this subject".** Every duration and count is nullable, and a
synthesised bucket is all-null, never all-zero. Writing ``idle_seconds: 0`` for an
hour nobody polled is the precise error ADR-0007 exists to prevent — it is how an
unpolled night becomes an idle night. A *real* bucket with short `observed_seconds`
keeps its genuine zeroes: those are measurements of a partly-covered hour, and
dividing by `observed_seconds` is what keeps them honest.

**Arrays are dense here rather than at the client.** ``(until - since) / 1h``
entries, ascending, gaps materialised as all-null buckets. Sparse would push
gap-filling to every consumer, each would do it differently, and one of them would
fill with zero — reintroducing the invented reading at the last hop, past every
test on this side of the wire.

**`observed_seconds` is the denominator of every ratio, never ``3600 × printers``.**
That is `rollups.py`'s own rule carried outward, and it has a consequence a client
must *render* rather than merely receive: with 12 of 50 machines reporting, a cell
reads 83% honestly-of-what-was-measured where the naive figure reads 20% by
asserting the 38 silent machines were idle. Hence `printers_reporting` on every
farm bucket — and no `capacity_seconds` anywhere, because the roster is today's, a
machine bought yesterday did not exist last Tuesday, and a historical capacity
would be a fabricated denominator.

**Seconds only. Never money, never kWh.** ``printing_seconds ×
amortization_per_hour`` and ``× nominal_power_kw`` are each one multiplication
away, and both belong behind `VIEW_FINANCIALS` in a composition above this layer —
CLAUDE.md keeps that permission apart from `VIEW_PRODUCTION` on purpose. `rollups.py`
also forbids re-deriving P&L from a table bucketed on `created_at`: the clock here
is when the farm *recorded* a state, not when the machine entered it, and phase 6's
electricity wants `observed_at` on the raw sample instead.

**Aggregated in SQL, dense-filled in Python**, for the reason `rollups.py` gives —
printers × hours is unbounded, unlike `throughput`'s job count.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.measure_sql import Grain, fleet_statement, printer_statement
from printorian.contexts.fleet.rollups import hour_start, latest_bucket
from printorian.core.errors import ValidationError
from printorian.core.ids import EntityId

#: Most buckets one ``grain=hour`` response will carry — 31 days.
#:
#: **Rejected past this, not truncated**, deliberately unlike `THROUGHPUT_LIMIT`,
#: which floors a scalar and flags it. That works for a number. A heat map exists
#: to show a *shape*, and a silently missing left edge is a lie about the shape.
MAX_BUCKETS = 744

#: The ceiling at any grain — 366 days, as a **response-size policy**.
#:
#: Stated rather than inherited from `telemetry_retention_days`, because
#: `metric_rollups` has no retention and is meant to be kept for ever: this table
#: will outlive the samples, and the ceiling must not silently follow them down.
MAX_WINDOW_HOURS = 366 * 24

_LOAD_PLACES = Decimal("0.01")


class MetricWindow(BaseModel):
    """The window actually read — aligned and clamped, not the one asked for."""

    since: datetime
    until: datetime
    grain: Grain


class MeasuredBucket(BaseModel):
    """One subject, one bucket, with the absence rule applied to every field.

    Every measurement is optional and defaults to ``None``. That default *is* the
    synthesised bucket: a gap in the summary comes back all-null, so no consumer
    can mistake an hour nobody polled for an hour nothing happened in.
    """

    #: The UTC hour. At ``grain=total`` this is `MetricWindow.since`.
    bucket_start: datetime

    #: **The denominator of every percentage**, per the rollups docstring — the
    #: time in the window some sample actually spoke for. Never ``3600 × printers``.
    observed_seconds: Decimal | None = None

    offline_seconds: Decimal | None = None
    idle_seconds: Decimal | None = None
    preparing_seconds: Decimal | None = None
    printing_seconds: Decimal | None = None
    paused_seconds: Decimal | None = None
    finished_seconds: Decimal | None = None
    #: Time spent *in* `PrinterState.ERROR` — not the same question as
    #: `error_sample_count`, which counts samples carrying a code whatever the
    #: state was. A machine can report a code while printing perfectly well.
    error_seconds: Decimal | None = None
    maintenance_seconds: Decimal | None = None

    #: How often the reported state differed from the previous observation. It is
    #: what separates a machine that thrashed between error and printing all hour
    #: from one that changed once, which the durations alone cannot say.
    state_changes: int | None = None
    error_sample_count: int | None = None

    #: ``printing_seconds / observed_seconds``, 0..1. ``None`` when there is no
    #: denominator — an unsummarised bucket, or one that observed nothing.
    load: Decimal | None = None


class FleetBucket(MeasuredBucket):
    """The whole farm's bucket: every printer that reported, summed."""

    #: Distinct printers with a row in this bucket. On the wire because the client
    #: has to be able to show coverage: 95% load with 3 of 50 machines reporting
    #: must not look like 95% with 50 of 50.
    printers_reporting: int | None = None


class PrinterBucket(MeasuredBucket):
    """One machine's bucket, and the fields that only mean something for one.

    Different shape from `FleetBucket` on purpose — that is why there are two
    routes rather than a `printer_id` filter on one. Temperatures and error codes
    are meaningful here and meaningless summed across a farm; `printers_reporting`
    is meaningless here.
    """

    #: Raw samples whose `created_at` fell in the bucket. **0 is real and
    #: meaningful**: a machine polled at 09:59:58 and not again until 10:15 carries
    #: seconds into the 10:00 bucket while contributing nothing to its counts.
    #: ``None`` is the different claim — the hour was never summarised.
    sample_count: int | None = None

    #: Passed through untouched and **never coalesced**. Null means not measured;
    #: `samples.sample_of` refuses to invent a cold bed and this must not undo it.
    #: Clients render the kit's `hv-faint` «—», never ``0 °C``.
    #:
    #: The averages are ``None`` at ``grain=total`` — `measure_sql` says why. A
    #: bare average is also uninterpretable alone, an hour idle for fifty minutes
    #: averaging a cold nozzle, which is why `printing_seconds` sits beside it.
    nozzle_temp_avg_c: Decimal | None = None
    nozzle_temp_max_c: Decimal | None = None
    bed_temp_avg_c: Decimal | None = None
    bed_temp_max_c: Decimal | None = None

    #: ``{code: occurrences}``. ``{}`` when the bucket was summarised and carried
    #: no codes; ``None`` when it was never summarised. Read for display, not
    #: filtered on (ADR-0017); the client names the codes (ADR-0012).
    error_codes: dict[str, int] | None = None


class FleetMetrics(BaseModel):
    """The farm's measured occupancy over a window."""

    window: MetricWindow
    #: The last hour anything has been summarised for, farm-wide. Lets a panel
    #: print «ДАННЫЕ ДО 14:00», and lets an operator see a stalled maintenance
    #: sweep instead of reading its silence as an idle farm.
    latest_bucket: datetime | None = None
    buckets: list[FleetBucket] = Field(default_factory=list)


class PrinterMetrics(BaseModel):
    """One machine's measured history over a window."""

    printer_id: EntityId
    window: MetricWindow
    latest_bucket: datetime | None = None
    buckets: list[PrinterBucket] = Field(default_factory=list)


# ------------------------------------------------------------------- the window


def resolve_window(
    *, since: datetime, until: datetime | None, grain: Grain, now: datetime
) -> MetricWindow:
    """Align, clamp and bound one request's window — the same rule for both routes.

    Half-open ``[since, until)``, both ends truncated **down** to the UTC hour.

    ``until`` defaults to, and is clamped down to, the start of the current hour.
    The sweep never writes the open hour (rollups.py: "A partial bucket that
    nothing rewrites is a permanently wrong row"), so serving it from raw samples
    here would make the newest heat cell computed by a different rule than the
    other 167 — a discontinuity at the exact cell people look at first.

    One rule for both routes, so a client that learned the window semantics once
    does not have to learn them twice.
    """
    open_hour = hour_start(now)
    start = _aligned(since, field="since")
    end = open_hour if until is None else min(_aligned(until, field="until"), open_hour)

    if end <= start:
        raise ValidationError(
            "error.fleet.metrics.window_empty", since=start.isoformat(), until=end.isoformat()
        )

    hours = int((end - start) / timedelta(hours=1))
    ceiling = MAX_BUCKETS if grain is Grain.HOUR else MAX_WINDOW_HOURS
    if hours > ceiling:
        raise ValidationError(
            "error.fleet.metrics.window_too_wide",
            max_hours=str(ceiling),
            requested_hours=str(hours),
        )
    return MetricWindow(since=start, until=end, grain=grain)


def _aligned(moment: datetime, *, field: str) -> datetime:
    """The hour ``moment`` falls in, refusing a timestamp that names no zone.

    A naive datetime is not a UTC one — it is an instant nobody stated. Assuming
    UTC would shift a Moscow client's window by three hours and return a perfectly
    plausible grid for the wrong day, which is the worst kind of wrong answer.
    """
    if moment.tzinfo is None:
        raise ValidationError("error.fleet.metrics.naive_timestamp", field=field)
    return hour_start(moment)


# -------------------------------------------------------------------- the reads


async def fleet_metrics(db: AsyncSession, window: MetricWindow) -> FleetMetrics:
    """The farm's buckets, plus the watermark that says how current they are."""
    return FleetMetrics(
        window=window,
        latest_bucket=await latest_bucket(db),
        buckets=await fleet_buckets(db, window),
    )


async def printer_metrics(
    db: AsyncSession, window: MetricWindow, *, printer_id: EntityId
) -> PrinterMetrics:
    """One machine's buckets. **The caller checks the id against the registry first.**

    `metric_rollups.printer_id` is deliberately not a foreign key, so an unchecked
    typo returns a dense all-null grid — which renders as "this machine did
    nothing". The most expensive ADR-0007 violation in the design is the one that
    looks like a successful response, which is why the check is the route's first
    act and not this function's guess about what an empty result means.
    """
    return PrinterMetrics(
        printer_id=printer_id,
        window=window,
        latest_bucket=await latest_bucket(db),
        buckets=await printer_buckets(db, window, printer_id=printer_id),
    )


async def fleet_buckets(db: AsyncSession, window: MetricWindow) -> list[FleetBucket]:
    """Dense, ascending farm buckets. Also what the dashboard's heat grid reads."""
    rows = await db.execute(text(fleet_statement(window.grain)), _bounds(window))
    measured = [FleetBucket(**_with_load(dict(row))) for row in rows.mappings()]
    return _dense(measured, window, lambda start: FleetBucket(bucket_start=start))


async def printer_buckets(
    db: AsyncSession, window: MetricWindow, *, printer_id: EntityId
) -> list[PrinterBucket]:
    """Dense, ascending buckets for one machine."""
    bounds = {**_bounds(window), "printer_id": str(printer_id)}
    rows = await db.execute(text(printer_statement(window.grain)), bounds)
    measured = [PrinterBucket(**_with_load(dict(row))) for row in rows.mappings()]
    return _dense(measured, window, lambda start: PrinterBucket(bucket_start=start))


def _bounds(window: MetricWindow) -> dict[str, Any]:
    return {"since": window.since, "until": window.until}


def _with_load(values: dict[str, Any]) -> dict[str, Any]:
    """Attach the one ratio this module computes, from the one denominator.

    Computed beside the row rather than left to a client, because the alternative
    is every panel dividing by whatever it has to hand — and ``3600 × printers`` is
    always to hand.
    """
    printing, observed = values.get("printing_seconds"), values.get("observed_seconds")
    if printing is not None and observed:
        values["load"] = (Decimal(printing) / Decimal(observed)).quantize(_LOAD_PLACES)
    return values


def _dense[B: MeasuredBucket](
    measured: list[B], window: MetricWindow, blank: Callable[[datetime], B]
) -> list[B]:
    """Exactly one entry per hour of the window — or exactly one, at ``total``.

    Every consumer gets the same ruler, and a hole in the summary arrives as an
    all-null bucket rather than as a shorter array somebody has to interpret.
    """
    if window.grain is Grain.TOTAL:
        return measured or [blank(window.since)]

    found = {row.bucket_start.astimezone(UTC): row for row in measured}
    dense: list[B] = []
    start = window.since
    while start < window.until:
        row = found.get(start)
        dense.append(row if row is not None else blank(start))
        start += timedelta(hours=1)
    return dense


__all__ = [
    "MAX_BUCKETS",
    "MAX_WINDOW_HOURS",
    "FleetBucket",
    "FleetMetrics",
    "Grain",
    "MeasuredBucket",
    "MetricWindow",
    "PrinterBucket",
    "PrinterMetrics",
    "fleet_buckets",
    "fleet_metrics",
    "printer_buckets",
    "printer_metrics",
    "resolve_window",
]
