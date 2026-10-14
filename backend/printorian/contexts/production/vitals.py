"""Three readings of production for the Prometheus scrape (INFRASTRUCTURE §5).

`api/routers/metrics.py` composes them; they live here because production owns
the facts. Each answers a question a person would otherwise have to go and
look at the console to answer:

* **`stuck_seconds`** — how long the oldest job has sat in each state where
  sitting means something is wedged: ASSIGNED (planned, never sent) and
  DISPATCHING (an upload or a start that never confirmed). Not PRINTING, where
  twelve hours is a normal plate, and not READY, which is the scheduler's
  queue and is meant to wait.
* **`awaiting_prep`** — the depth of the prep queue: jobs no engineer has
  sliced. ADR-0006 names this as the number to watch.
* **`variance_ratio`** — how the slicer's truth compares with the quote, over
  the variances recorded in a window: the mean of `prepared_cost /
  quoted_cost`, the quantity ADR-0013's band is drawn on. A ratio carries no
  rouble amount, which is what lets it sit on an unauthenticated scrape.

**Every age is measured on the database's clock, not the application's.**
`job_events.created_at` and `print_jobs.updated_at` are written by PostgreSQL's
`now()`, so the age is `now() - that` in one place and one clock. Mixing in the
injected `Clock` would compare two clocks and call the difference a duration.

**Zero and absent mean different things here, and each is used for its own
fact (ADR-0007).** A state with no job in it reads 0 seconds — the query ran
and found nothing stuck, which is a measurement. A window with no variances
reads *no* ratio: the mean of nothing is not 1.0 and not 0, and the count
beside it says how many observations the mean stands on.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import EstimateVariance, JobEvent, PrintJob
from printorian.contexts.production.policies import JobStatus

#: The states in which time passing means something has gone wrong.
STUCK_WATCHED: tuple[JobStatus, ...] = (JobStatus.ASSIGNED, JobStatus.DISPATCHING)

#: How far back the variance mean looks. Long enough that a farm slicing a
#: handful of plates a week still has a mean to show; short enough that a
#: re-calibrated estimator stops being blamed for last quarter's mistakes.
VARIANCE_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class VarianceReading:
    #: Mean of prepared over quoted cost, or ``None`` when nothing was recorded.
    ratio: float | None
    #: The observations the mean stands on — the denominator, published beside it.
    samples: int


async def stuck_seconds(db: AsyncSession) -> dict[JobStatus, float]:
    """The age of the oldest job in each watched state, on the database clock.

    A job's time in its state is measured from its newest event *into* that
    state. A job with no such event — created directly in the state, or older
    than the event log — falls back to its row's `updated_at`, which is the last
    time anything about it changed: for a job that is wedged, nothing has. The
    fallback exists so that such a job cannot make its state read "nothing
    stuck", which would be the flattering zero CLAUDE.md §1 forbids.
    """
    entered = (
        select(JobEvent.job_id, func.max(JobEvent.created_at).label("entered_at"))
        .join(PrintJob, PrintJob.id == JobEvent.job_id)
        .where(PrintJob.status.in_(STUCK_WATCHED), JobEvent.to_status == PrintJob.status)
        .group_by(JobEvent.job_id)
        .subquery()
    )
    since = func.coalesce(entered.c.entered_at, PrintJob.updated_at)
    rows = await db.execute(
        select(PrintJob.status, func.max(func.extract("epoch", func.now() - since)))
        .outerjoin(entered, entered.c.job_id == PrintJob.id)
        .where(PrintJob.status.in_(STUCK_WATCHED))
        .group_by(PrintJob.status)
    )
    ages = dict.fromkeys(STUCK_WATCHED, 0.0)
    for state, seconds in rows.all():
        ages[JobStatus(state)] = max(0.0, float(seconds or 0))
    return ages


async def awaiting_prep(db: AsyncSession) -> int:
    """How many jobs are waiting for an engineer to slice them (ADR-0006)."""
    count = await db.scalar(
        select(func.count()).select_from(PrintJob).where(PrintJob.status == JobStatus.PENDING)
    )
    return int(count or 0)


async def variance_ratio(db: AsyncSession, *, days: int = VARIANCE_WINDOW_DAYS) -> VarianceReading:
    """Mean of `prepared_cost / quoted_cost` over the variances of the last ``days``.

    Rows quoted at nothing are left out rather than divided by: a zero quote is
    a test fixture or a free reprint, and either would put an infinity in the mean.
    """
    row = (
        await db.execute(
            select(
                func.avg(EstimateVariance.prepared_cost / EstimateVariance.quoted_cost),
                func.count(),
            ).where(
                EstimateVariance.quoted_cost > 0,
                EstimateVariance.created_at >= func.now() - func.make_interval(0, 0, 0, days),
            )
        )
    ).one()
    mean, samples = row
    return VarianceReading(ratio=None if mean is None else float(mean), samples=int(samples or 0))


__all__ = [
    "STUCK_WATCHED",
    "VARIANCE_WINDOW_DAYS",
    "VarianceReading",
    "awaiting_prep",
    "stuck_seconds",
    "variance_ratio",
]
