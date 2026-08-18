"""The prep queue and the variance rule.

ADR-0006 puts slicing after checkout and caches its output, so the first order of a
configuration waits for an engineer and every later one does not. ADR-0013 says the
truth that slicing produces may cost more than the quote — and that past a
configured tolerance the job stops rather than quietly eating the difference.

Both rules live here, next to the job they act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus


#: How the numbers a job is working from were arrived at (ADR-0013).
class EstimateSource:
    """Not an enum on the job: it is derived from what the job has."""

    #: Priced from geometry before anything was sliced.
    MESH_HEURISTIC = "mesh_heuristic"
    #: Exact print minutes and per-slot grams from the slicer.
    PREPARED_PLATE = "prepared_plate"
    #: What the machine actually did. Phase 6 territory.
    MEASURED = "measured"


@dataclass(frozen=True, slots=True, kw_only=True)
class VarianceVerdict:
    """Whether a prepared plate's cost may be absorbed, and by how much it missed."""

    quoted_cost: Decimal
    prepared_cost: Decimal
    tolerance: Decimal
    within_tolerance: bool

    @property
    def delta(self) -> Decimal:
        return self.prepared_cost - self.quoted_cost

    @property
    def ratio(self) -> Decimal:
        """Overrun as a fraction of the quote. Zero when the quote was free."""
        if self.quoted_cost <= 0:
            return Decimal(0)
        return self.delta / self.quoted_cost


def assess_variance(
    *, quoted_cost: Decimal, prepared_cost: Decimal, tolerance: Decimal
) -> VarianceVerdict:
    """Apply ADR-0013's band.

    Deliberately one-sided. A plate that turns out **cheaper** than quoted is not a
    problem to escalate — the quote is what the customer agreed to and the farm
    keeps the difference, exactly as it absorbs a small overrun. Only an overrun
    beyond the band stops the job.

    A quote of zero cannot be exceeded by a percentage, so it is treated as within
    tolerance rather than as an infinite overrun; a free job is a decision someone
    already made.
    """
    if quoted_cost <= 0:
        within = True
    else:
        overrun = (prepared_cost - quoted_cost) / quoted_cost
        within = overrun <= tolerance
    return VarianceVerdict(
        quoted_cost=quoted_cost,
        prepared_cost=prepared_cost,
        tolerance=tolerance,
        within_tolerance=within,
    )


async def pending_jobs(db: AsyncSession) -> list[PrintJob]:
    """The prep queue: jobs with no plate yet (ADR-0006).

    Not a separate entity — the queue *is* the jobs an engineer has not sliced.
    Its depth is the metric ADR-0006 says to watch: if it saturates, that is the
    signal to reopen headless slicing.

    Undated work sorts last, so a job carrying a promise is the one picked up
    first. Events are eager-loaded because the view carries them and a lazy read
    here would be implicit IO in async context.
    """
    return list(
        await db.scalars(
            select(PrintJob)
            .where(PrintJob.status == JobStatus.PENDING)
            .options(selectinload(PrintJob.events))
            .order_by(PrintJob.due_at.is_(None), PrintJob.due_at, PrintJob.created_at)
        )
    )


async def assigned_jobs(db: AsyncSession) -> list[PrintJob]:
    """Jobs the planner has placed but nothing has been sent to yet."""
    return list(
        await db.scalars(
            select(PrintJob)
            .where(PrintJob.status == JobStatus.ASSIGNED)
            .options(selectinload(PrintJob.events))
            .order_by(PrintJob.due_at.is_(None), PrintJob.due_at, PrintJob.created_at)
        )
    )


async def queued_minutes_by_printer(db: AsyncSession) -> dict[str, Decimal]:
    """Work already committed to each machine, for the planner's load balancing.

    Counts everything a printer is holding — assigned, dispatching or printing.
    Leaving out the running job would make a machine two hours into a print look
    as free as one standing idle.
    """
    rows = await db.execute(
        select(PrintJob.printer_id, func.sum(PrintJob.estimated_minutes))
        .where(
            PrintJob.printer_id.is_not(None),
            PrintJob.status.in_([JobStatus.ASSIGNED, JobStatus.DISPATCHING, JobStatus.PRINTING]),
        )
        .group_by(PrintJob.printer_id)
    )
    return {str(printer_id): Decimal(total or 0) for printer_id, total in rows}
