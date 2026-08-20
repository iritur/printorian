"""What each machine is doing for the next twelve hours, and what the queue has
already spent.

Two reads the dashboard needs and nothing else does, so they live here rather than
in the service — which is at the length gate — and beside `reads.py`, which holds
the floor's other bounded queries.

**This is not a plan.** The planner (ARCHITECTURE §6) decides assignments; this
only lays out what it already decided, on a time axis. A schedule view that ran
the planner would show a different farm every time it was opened, and an operator
would have no way to tell a re-plan from a machine actually changing its mind.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import PrintJob, WaitListEntry
from printorian.contexts.production.policies import JobStatus
from printorian.core.ids import EntityId

#: How far ahead the schedule strip reaches. Twelve hours is one long print plus
#: the one behind it — far enough to answer "when does a machine free up", short
#: enough that the far end is still a prediction anyone would act on.
HORIZON_HOURS = 12

#: Most bars drawn. A farm with more queued work than this has a backlog the
#: schedule strip cannot usefully render; the order desk is where that is read.
SCHEDULE_LIMIT = 200

#: Jobs holding or waiting for a machine. `PENDING` and `ON_HOLD` are absent:
#: nothing can be laid on an axis for a job that has no plate and no machine.
SCHEDULED: frozenset[JobStatus] = frozenset(
    {JobStatus.READY, JobStatus.ASSIGNED, JobStatus.DISPATCHING, JobStatus.PRINTING}
)


class ScheduleBar(BaseModel):
    """One job's stretch of one machine's time."""

    job_id: EntityId
    order_id: EntityId
    #: The customer-facing order number.
    #:
    #: Left empty here and filled by the delivery layer: production knows which
    #: order a job belongs to and not what that order is *called*, and reaching
    #: across to ordering for a label would be exactly the coupling the context
    #: boundary exists to prevent. A bar labelled with a raw id is unreadable on
    #: a screen an operator uses to find work, so somebody has to resolve it —
    #: and the delivery layer is where composition is allowed.
    order_number: str = ""
    #: A `JobStatus` value, so the client can draw printing and queued differently.
    status: JobStatus
    #: The prepared plate's filename, when there is one.
    label: str
    starts_at: datetime
    ends_at: datetime
    #: Present only while the machine is actually running the job.
    progress_percent: int | None = None


class ScheduleRow(BaseModel):
    """One machine's next twelve hours."""

    printer_id: EntityId
    bars: list[ScheduleBar] = Field(default_factory=list)
    #: When this machine has nothing left to do, or ``None`` if its queue runs past
    #: the horizon. The dashboard's "first free machine" line reads these.
    free_at: datetime | None = None


class Schedule(BaseModel):
    """The strip, with the axis it was drawn against."""

    starts_at: datetime
    ends_at: datetime
    rows: list[ScheduleRow] = Field(default_factory=list)


class CommittedMaterial(BaseModel):
    """Mass the queue has already spoken for, per material code."""

    material_code: str
    grams: Decimal
    job_count: int


async def schedule(
    db: AsyncSession, *, now: datetime, hours: int = HORIZON_HOURS, limit: int = SCHEDULE_LIMIT
) -> Schedule:
    """Lay the assigned queue out on a time axis.

    Times are *derived*, not stored: a job's bar starts when the one before it on
    the same machine ends, and lasts its estimate. That is exactly how the farm
    will actually run, and it is honest about being an estimate — the only stored
    instant here is ``started_at`` on the job that is already running.

    Jobs with no machine yet (`READY`) are excluded: putting them on a row would
    claim the planner has made a decision it has not.
    """
    end = now + timedelta(hours=hours)
    jobs = list(
        await db.scalars(
            select(PrintJob)
            .where(PrintJob.status.in_(SCHEDULED), PrintJob.printer_id.is_not(None))
            # Running work first on each machine, then the queue in the order the
            # planner will take it — priority, then age.
            .order_by(
                PrintJob.printer_id,
                PrintJob.status != JobStatus.PRINTING,
                PrintJob.priority.desc(),
                PrintJob.created_at,
            )
            .limit(limit)
        )
    )

    rows: dict[EntityId, ScheduleRow] = {}
    for job in jobs:
        printer_id = job.printer_id
        if printer_id is None:  # pragma: no cover — excluded by the query above
            continue
        row = rows.setdefault(printer_id, ScheduleRow(printer_id=printer_id))
        starts_at = row.bars[-1].ends_at if row.bars else now
        ends_at = starts_at + _remaining(job)
        row.bars.append(
            ScheduleBar(
                job_id=job.id,
                order_id=job.order_id,
                status=job.status,
                label=job.plate_filename or "",
                starts_at=starts_at,
                ends_at=ends_at,
                progress_percent=(
                    job.progress_percent if job.status is JobStatus.PRINTING else None
                ),
            )
        )
        row.free_at = ends_at if ends_at <= end else None

    return Schedule(starts_at=now, ends_at=end, rows=list(rows.values()))


def _remaining(job: PrintJob) -> timedelta:
    """How much longer this job needs.

    A running job's remaining time is its estimate less the fraction already done,
    which is what makes the first bar on every row shrink as the machine works
    rather than restating the whole job every refresh. Progress the machine has not
    reported is treated as zero — under-reporting a finish time is the safe
    direction for a promise.
    """
    total = timedelta(minutes=float(job.estimated_minutes))
    if job.status is not JobStatus.PRINTING:
        return total
    done = max(0, min(100, job.progress_percent or 0))
    return total * ((100 - done) / 100)


async def committed_material(db: AsyncSession) -> list[CommittedMaterial]:
    """Filament the queue has already promised away.

    Counted over every job that has not printed yet — including `PENDING`, which
    has no plate: the material is spoken for the moment the order exists, and a
    headroom figure that only counted sliced work would show comfort right up to
    the point the slicing finished.
    """
    rows = await db.execute(
        select(
            PrintJob.material_type,
            func.coalesce(func.sum(PrintJob.grams_required), 0),
            func.count(PrintJob.id),
        )
        .where(
            PrintJob.status.in_(
                {
                    JobStatus.PENDING,
                    JobStatus.ON_HOLD,
                    JobStatus.READY,
                    JobStatus.ASSIGNED,
                    JobStatus.DISPATCHING,
                    JobStatus.PRINTING,
                }
            ),
            PrintJob.material_type != "",
        )
        .group_by(PrintJob.material_type)
    )
    return [
        CommittedMaterial(
            material_code=code, grams=Decimal(str(grams or 0)), job_count=int(count or 0)
        )
        for code, grams, count in rows.all()
    ]


async def wait_list_size(db: AsyncSession) -> int:
    """Jobs nothing can currently take.

    Counted from `WaitListEntry` rather than from a job status, because that is the
    row the planner writes when it finds no eligible machine — and it is the same
    number `reads.wait_list` renders, so the dashboard chip and the wait list can
    never disagree about how much is stuck.
    """
    value = await db.scalar(select(func.count(WaitListEntry.id)))
    return int(value or 0)


__all__ = [
    "HORIZON_HOURS",
    "SCHEDULED",
    "SCHEDULE_LIMIT",
    "CommittedMaterial",
    "Schedule",
    "ScheduleBar",
    "ScheduleRow",
    "committed_material",
    "schedule",
    "wait_list_size",
]
