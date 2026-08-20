"""How much the farm actually printed, and how much of it came out right.

The dashboard's printer KPIs. Every figure here is counted from jobs that have
already finished — nothing is projected, and nothing is inferred from a machine's
current state. Rule 3 of the project applies to more than drivers: a summary that
guessed at run hours would be a driver simulating silently with extra steps.

Idle is the one figure that has to be *derived* rather than counted, and it is
derived by subtraction: capacity is machines × hours, run hours are recorded, and
what is left is idle. That makes it honest about what it is — a residual, not an
observation — and it is why the machine count comes in as an argument instead of
production reaching into the fleet's tables for it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus

#: How many days the load map covers. Seven, so a week's shape is visible and
#: Saturday can be compared with Tuesday.
HEAT_DAYS = 7

#: Most finished jobs one window will be summed over. A quarter on a busy farm
#: stays well inside this; beyond it the KPI is reported from the newest jobs and
#: the caller is told, rather than the query being allowed to grow without bound.
THROUGHPUT_LIMIT = 5_000


class HeatRow(BaseModel):
    """One weekday of the load map: 24 hours, each 0..1 of the farm's capacity."""

    #: Monday is 0, matching `datetime.weekday()`. The client names it.
    weekday: int
    hours: list[Decimal] = Field(default_factory=list)


class Throughput(BaseModel):
    """What the machines did over one window."""

    #: Hours of actual printing, summed from finished jobs' own start and end.
    run_hours: Decimal
    #: Machines × window length. The ceiling run hours are measured against.
    capacity_hours: Decimal
    #: Capacity minus run hours, floored at zero.
    idle_hours: Decimal
    succeeded: int
    failed: int
    #: Successful prints as a share of finished ones. ``None`` when nothing
    #: finished — a farm that printed nothing has no quality figure, and showing
    #: 100% for that is the most misleading number a dashboard can carry.
    success_percent: Decimal | None = None
    #: True when the window held more finished jobs than were summed. The client
    #: says so rather than presenting a floor as a total.
    truncated: bool = False


async def throughput(
    db: AsyncSession, *, since: datetime, until: datetime, machines: int
) -> Throughput:
    """Recorded printing between two instants.

    Attributed by finish time. A print that began yesterday and ended this morning
    counts wholly to today, which slightly over-credits the day it lands on — the
    alternative, splitting a job across the boundary, requires knowing when a
    machine was actually running rather than when the job was open, and the farm
    does not record that. Over a window of a day or more the effect cancels.

    Durations are summed in Python rather than with ``EXTRACT(EPOCH ...)``. The
    row count is bounded above, the arithmetic is `timedelta` either way, and
    keeping it here is what lets the boundary rule above be stated in one readable
    place instead of inside a SQL expression.
    """
    rows = (
        await db.execute(
            select(PrintJob.status, PrintJob.started_at, PrintJob.finished_at)
            .where(
                PrintJob.finished_at.is_not(None),
                PrintJob.finished_at >= since,
                PrintJob.finished_at < until,
            )
            .order_by(PrintJob.finished_at.desc())
            .limit(THROUGHPUT_LIMIT + 1)
        )
    ).all()

    truncated = len(rows) > THROUGHPUT_LIMIT
    run = timedelta()
    succeeded = failed = 0
    for status, started_at, finished_at in rows[:THROUGHPUT_LIMIT]:
        if started_at is not None and finished_at is not None and finished_at > started_at:
            run += finished_at - started_at
        if status == JobStatus.SUCCEEDED:
            succeeded += 1
        elif status == JobStatus.FAILED:
            failed += 1

    capacity = Decimal(machines) * Decimal((until - since) / timedelta(hours=1))
    return _assemble(
        run_hours=Decimal(run / timedelta(hours=1)),
        succeeded=succeeded,
        failed=failed,
        capacity_hours=capacity,
        truncated=truncated,
    )


def _assemble(
    *,
    run_hours: Decimal,
    succeeded: int,
    failed: int,
    capacity_hours: Decimal,
    truncated: bool,
) -> Throughput:
    finished = succeeded + failed
    return Throughput(
        run_hours=run_hours.quantize(Decimal("0.1")),
        capacity_hours=capacity_hours.quantize(Decimal("0.1")),
        idle_hours=max(Decimal(0), capacity_hours - run_hours).quantize(Decimal("0.1")),
        succeeded=succeeded,
        failed=failed,
        success_percent=(
            (Decimal(succeeded) / Decimal(finished) * 100).quantize(Decimal("0.1"))
            if finished
            else None
        ),
        truncated=truncated,
    )


__all__ = ["HEAT_DAYS", "THROUGHPUT_LIMIT", "HeatRow", "Throughput", "hourly_load", "throughput"]


async def hourly_load(
    db: AsyncSession, *, now: datetime, machines: int, days: int = HEAT_DAYS
) -> list[HeatRow]:
    """How busy the farm was, hour by hour, over the last week.

    The kit's load map: seven rows of twenty-four cells, brighter meaning busier.
    Its point is the shape rather than any one cell — a farm that is idle every
    night is a farm with capacity nobody is selling, and that is invisible in a
    daily total.

    A job contributes to every hour it overlapped, in proportion to how much of
    that hour it covered, so an eight-hour print lights eight cells rather than
    the one it happened to finish in. Value is machine-hours over capacity, and
    it is clamped at 1: two machines printing through one hour on a two-machine
    farm is full, not 200%.
    """
    if machines <= 0:
        return []

    first = (now - timedelta(days=days - 1)).replace(minute=0, second=0, microsecond=0)
    first = first.replace(hour=0)
    jobs = (
        await db.execute(
            select(PrintJob.started_at, PrintJob.finished_at).where(
                PrintJob.started_at.is_not(None),
                # Anything that overlapped the window, not only what finished in
                # it: a print still running now is exactly what "busy" means.
                or_(PrintJob.finished_at.is_(None), PrintJob.finished_at >= first),
                PrintJob.started_at < now,
            )
        )
    ).all()

    # (day offset, hour) -> machine-minutes
    spent: dict[tuple[int, int], float] = {}
    for started_at, finished_at in jobs:
        ended = finished_at or now
        for offset in range(days):
            midnight = first + timedelta(days=offset)
            for hour in range(24):
                bucket_start = midnight + timedelta(hours=hour)
                bucket_end = bucket_start + timedelta(hours=1)
                overlap = min(ended, bucket_end) - max(started_at, bucket_start)
                if overlap > timedelta():
                    key = (offset, hour)
                    spent[key] = spent.get(key, 0.0) + overlap / timedelta(minutes=1)

    rows: list[HeatRow] = []
    for offset in range(days):
        midnight = first + timedelta(days=offset)
        capacity = float(machines) * 60.0
        rows.append(
            HeatRow(
                weekday=midnight.weekday(),
                hours=[
                    Decimal(str(min(1.0, spent.get((offset, hour), 0.0) / capacity))).quantize(
                        Decimal("0.01")
                    )
                    for hour in range(24)
                ],
            )
        )
    return rows
