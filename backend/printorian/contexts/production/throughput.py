"""How much of what the farm printed came out right.

The dashboard's quality KPIs. Every figure here is counted from jobs that have
already finished — nothing is projected, and nothing is inferred from a machine's
current state. Rule 3 of the project applies to more than drivers: a summary that
guessed at outcomes would be a driver simulating silently with extra steps.

**Occupancy is not here any more, and that is the point of the split.** This module
used to carry `run_hours`, `capacity_hours`, `idle_hours` and the 7 × 24 load map,
all of them derived from `print_jobs` — which records when a job was *booked*, not
when a machine was *running*. A paused or errored print still counted as run time,
a job row that was never closed counted from its start to `now` for ever, machine
time with no job behind it was invisible, and idle was a residual against today's
roster rather than an observation. `metric_rollups` measures all four properly, so
they moved to `contexts.fleet.occupancy` and were deleted here in the same change:
two functions called "the load map" with different denominators is the second
number nobody can reconcile that this codebase keeps warning about.

The durable split the deletion leaves behind: **telemetry knows occupancy, jobs
know outcomes.** A failed print and a successful one both report PRINTING for their
whole duration, so quality can never come from telemetry — which is exactly why
`succeeded` / `failed` / `success_percent` stay here and stay on `print_jobs`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus

#: Most finished jobs one window will be counted over. A quarter on a busy farm
#: stays well inside this; beyond it the KPI is reported from the newest jobs and
#: the caller is told, rather than the query being allowed to grow without bound.
THROUGHPUT_LIMIT = 5_000


class Throughput(BaseModel):
    """What the machines produced over one window, as outcomes."""

    succeeded: int
    failed: int
    #: Successful prints as a share of finished ones. ``None`` when nothing
    #: finished — a farm that printed nothing has no quality figure, and showing
    #: 100% for that is the most misleading number a dashboard can carry.
    success_percent: Decimal | None = None
    #: True when the window held more finished jobs than were counted. The client
    #: says so rather than presenting a floor as a total.
    truncated: bool = False


async def throughput(db: AsyncSession, *, since: datetime, until: datetime) -> Throughput:
    """Finished prints between two instants, split by how they ended.

    Attributed by finish time. A print that began yesterday and ended this morning
    counts wholly to today, which slightly over-credits the day it lands on — and
    matters far less here than it did when this function also reported hours, since
    an outcome genuinely belongs to one moment in a way a duration does not.
    """
    rows = (
        await db.execute(
            select(PrintJob.status)
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
    succeeded = sum(1 for (status,) in rows[:THROUGHPUT_LIMIT] if status == JobStatus.SUCCEEDED)
    failed = sum(1 for (status,) in rows[:THROUGHPUT_LIMIT] if status == JobStatus.FAILED)

    finished = succeeded + failed
    return Throughput(
        succeeded=succeeded,
        failed=failed,
        success_percent=(
            (Decimal(succeeded) / Decimal(finished) * 100).quantize(Decimal("0.1"))
            if finished
            else None
        ),
        truncated=truncated,
    )


__all__ = ["THROUGHPUT_LIMIT", "Throughput", "throughput"]
