"""The job journal: every step a job took, in order.

A module-level function rather than a service method so the planner's persistence
can write to the same journal without reaching back into the service — the two
would otherwise have to know about each other.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import JobEvent, PrintJob
from printorian.contexts.production.policies import JobStatus


async def record_event(
    db: AsyncSession,
    job: PrintJob,
    status: JobStatus,
    *,
    reason: str,
    previous: JobStatus | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """Append one step to a job's history."""
    # Counted with a query rather than `len(job.events)`: the relationship is lazy,
    # and touching it on an instance that was not eagerly loaded is an implicit IO
    # call in async context — a `MissingGreenlet` rather than a number.
    count = await db.scalar(
        select(func.count()).select_from(JobEvent).where(JobEvent.job_id == job.id)
    )
    db.add(
        JobEvent(
            job_id=job.id,
            sequence=count or 0,
            from_status=previous.value if previous else None,
            to_status=status.value,
            reason=reason,
            details=details or {},
        )
    )
    await db.flush()
