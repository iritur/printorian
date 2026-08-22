"""The dashboard's two shapes, cut from the measured hours.

`measures.py` emits the wire contract for `/fleet/metrics`: seconds, one bucket per
hour, absence as ``None``. The summary screen wants two *reshapings* of exactly
that data — a 7 × 24 grid, and one window's hours beside the window before it — and
they live here so both are computed from the same reader rather than from a second
idea of what the farm did.

**These replace `production.throughput`'s versions, and the numbers change.** That
module measured *booked* machine-hours from `print_jobs`: a job row saying PRINTING
lit its cell at full brightness while the machine was paused, errored or offline;
one never-closed job row painted every cell from its start to `now`; machine time
with no job behind it — a print pushed from the machine's own screen, a calibration
run, a maintenance heat soak — was invisible; and capacity was **today's** roster
applied to last week's hours. Idle was a residual, `capacity - run`, which is why a
job that ran past `THROUGHPUT_LIMIT` silently inflated it.

Here both are measurements. Cells generally go *down*, because booked ≥ run for any
print that paused, errored or waited on an operator, and a farm where finished
plates sit overnight will see night cells drop hard — `finished_seconds` is its own
column and is not printing. **Unpolled hours stop being dark and become blank**,
which is the visible ADR-0007 change and the one that belongs in a release note: an
operator who has read "dark = idle" for months must now read the third treatment as
"unknown".

Two functions called "the load map" with different denominators is the second
number nobody can reconcile that this codebase keeps warning about, so
`throughput.hourly_load` was deleted in the change that added this rather than left
standing. `throughput()` keeps `succeeded` / `failed` / `success_percent`, which is
the durable split: **telemetry knows occupancy, jobs know outcomes.**
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.history import BUCKET_SECONDS
from printorian.contexts.fleet.measures import FleetBucket, Grain, MetricWindow, fleet_buckets
from printorian.contexts.fleet.rollups import hour_start

#: How many days the load map covers. Seven, so a week's shape is visible and
#: Saturday can be compared with Tuesday.
HEAT_DAYS = 7

_HOURS_PLACES = Decimal("0.1")


class HeatCell(BaseModel):
    """One cell of the load map, and the coverage behind it.

    ``load`` is ``None`` for an hour that was never summarised, which the client
    must give a **third** treatment — outline or hatch — distinct from both dark
    and bright. Dark means "measured, and the farm was idle"; blank means "nobody
    measured". Collapsing the two is the exact reading ADR-0007 forbids.

    `printers_reporting` ships per cell because the denominator is what was
    observed, not the roster: a bright cell can be bright because few machines
    reported rather than because many were busy, and the failure is silent and
    directional — the worse the polling, the healthier the farm looks.
    """

    load: Decimal | None = None
    printers_reporting: int = 0


class HeatRow(BaseModel):
    """One day of the load map: 24 cells."""

    #: Monday is 0, matching `datetime.weekday()`. The client names it.
    weekday: int
    hours: list[HeatCell] = Field(default_factory=list)


class Occupancy(BaseModel):
    """Where one window's machine-hours went, summed over the reporting printers.

    Hours rather than seconds because this is the KPI tile's own unit — and it is a
    composition for the summary screen, not the metrics wire contract, which stays
    in seconds. Everything is nullable for the usual reason: a window in which
    nothing was ever summarised has no occupancy figure, and zero would be a claim.
    """

    #: The window's denominator, and what «ИЗ 288 ВОЗМОЖНЫХ» should now read from.
    #: **Observed**, not roster × 24 — so the tile stops asserting that machines
    #: nobody polled were available.
    observed_hours: Decimal | None = None

    offline_hours: Decimal | None = None
    idle_hours: Decimal | None = None
    preparing_hours: Decimal | None = None
    printing_hours: Decimal | None = None
    paused_hours: Decimal | None = None
    finished_hours: Decimal | None = None
    error_hours: Decimal | None = None
    maintenance_hours: Decimal | None = None

    #: Distinct machines with any summarised hour in the window — the coverage
    #: figure the tile needs beside its percentage, for the reason `HeatCell` gives.
    printers_reporting: int | None = None
    #: ``printing_hours / observed_hours``, 0..1.
    load: Decimal | None = None


class FleetOccupancy(BaseModel):
    """One window's occupancy against the one before it.

    The previous half is what the `hv-kpi__d` delta chips have been drawing without
    anything behind them. Note the two halves of «Загрузка парка» then measure
    different things — a live count against a windowed ratio — so the client must
    label the delta as period-over-period, or the tile reads as "utilisation moved
    six points in the last second".
    """

    current: Occupancy = Field(default_factory=Occupancy)
    previous: Occupancy = Field(default_factory=Occupancy)


async def occupancy(db: AsyncSession, *, since: datetime, until: datetime) -> Occupancy:
    """One window's measured hours, as one ``grain=total`` read.

    Both ends are truncated down to the hour, the same cut `resolve_window` makes,
    because the sweep never writes the open hour. A window that holds no closed
    hour at all — the dashboard opened at 00:30 with `period=today` — returns an
    all-null `Occupancy` rather than raising: the summary must still render, and
    "nothing has been summarised yet" is an honest thing for a tile to say.
    """
    start, end = hour_start(since), hour_start(until)
    if end <= start:
        return Occupancy()

    window = MetricWindow(since=start, until=end, grain=Grain.TOTAL)
    return _as_hours((await fleet_buckets(db, window))[0])


async def hourly_load(db: AsyncSession, *, now: datetime, days: int = HEAT_DAYS) -> list[HeatRow]:
    """The kit's load map, re-sourced onto measured state.

    ``days × 24`` cells ending at the close of today, so the grid keeps its shape
    whatever time it is read at. The hours after `now` have not happened and the
    current one is never summarised, so they come back blank — which is the honest
    rendering and the reason the window here is built directly rather than through
    `resolve_window`, whose clamp exists to bound a *request* and would shorten the
    grid instead.

    Strictly cheaper than the loop it replaces: one indexed aggregate against 168
    buckets, rather than every overlapping job compared with all 168 in Python.
    """
    since = hour_start(now).replace(hour=0) - timedelta(days=days - 1)
    window = MetricWindow(since=since, until=since + timedelta(days=days), grain=Grain.HOUR)
    buckets = await fleet_buckets(db, window)
    return [
        HeatRow(
            weekday=(since + timedelta(days=offset)).weekday(),
            hours=[_cell(bucket) for bucket in buckets[offset * 24 : (offset + 1) * 24]],
        )
        for offset in range(days)
    ]


def _cell(bucket: FleetBucket) -> HeatCell:
    return HeatCell(load=bucket.load, printers_reporting=bucket.printers_reporting or 0)


def _as_hours(bucket: FleetBucket) -> Occupancy:
    """Seconds into hours, and only here — the metrics endpoints emit seconds.

    Written out rather than derived from `STATE_COLUMNS`, because a mapping built
    by name would let a ninth state land in an `Occupancy` field that does not
    exist and be silently dropped. Here a ninth state fails to compile.
    """
    return Occupancy(
        observed_hours=_hours(bucket.observed_seconds),
        offline_hours=_hours(bucket.offline_seconds),
        idle_hours=_hours(bucket.idle_seconds),
        preparing_hours=_hours(bucket.preparing_seconds),
        printing_hours=_hours(bucket.printing_seconds),
        paused_hours=_hours(bucket.paused_seconds),
        finished_hours=_hours(bucket.finished_seconds),
        error_hours=_hours(bucket.error_seconds),
        maintenance_hours=_hours(bucket.maintenance_seconds),
        printers_reporting=bucket.printers_reporting,
        load=bucket.load,
    )


def _hours(seconds: Decimal | None) -> Decimal | None:
    if seconds is None:
        return None
    return (seconds / BUCKET_SECONDS).quantize(_HOURS_PLACES)


__all__ = [
    "HEAT_DAYS",
    "FleetOccupancy",
    "HeatCell",
    "HeatRow",
    "Occupancy",
    "hourly_load",
    "occupancy",
]
