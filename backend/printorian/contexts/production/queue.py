"""What a customer is told about where their work stands.

Deliberately separate from the floor's view. An operator needs every machine that
was considered and why each was refused; a customer needs to know whether their
thing is moving, and if not, whether waiting will fix it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import JobEvent, PrintJob, WaitListEntry
from printorian.contexts.production.policies import JobStatus
from printorian.contexts.production.schemas import QueuePosition
from printorian.core.ids import EntityId


async def queue_position(db: AsyncSession, order_id: EntityId) -> QueuePosition | None:
    """Where an order's work stands (C7), or nothing if it has no job yet.

    Position is counted only among jobs genuinely waiting for *capacity*. A job
    blocked on a person — filament nobody has mounted, or a plate no machine on
    the farm can print — is not in a queue that will drain, and numbering it
    "3rd" would promise movement that is not coming. Those get a reason and no
    number, which is the honest answer.
    """
    job = await db.scalar(
        select(PrintJob)
        .where(PrintJob.order_id == order_id)
        # The latest attempt: a remade job is the one the customer is waiting on.
        .order_by(PrintJob.created_at.desc())
        .limit(1)
    )
    if job is None:
        return None

    machine = {
        "attempt": job.attempt,
        "printer_id": job.printer_id,
        "assigned_at": await _first_entered(db, job.id, JobStatus.ASSIGNED),
        "started_at": job.started_at,
    }

    entry = await db.scalar(select(WaitListEntry).where(WaitListEntry.job_id == job.id))
    if entry is None:
        # Not waiting: assigned, printing, finished, or held.
        return QueuePosition(
            job_status=job.status, progress_percent=job.progress_percent, **machine
        )

    position: int | None = None
    if entry.predicted_start is not None:
        ahead = await db.scalar(
            select(func.count())
            .select_from(WaitListEntry)
            .where(
                WaitListEntry.predicted_start.is_not(None),
                WaitListEntry.predicted_start < entry.predicted_start,
            )
        )
        position = (ahead or 0) + 1

    return QueuePosition(
        job_status=job.status,
        position=position,
        reason=entry.reason,
        predicted_start=entry.predicted_start,
        progress_percent=job.progress_percent,
        **machine,
    )


async def _first_entered(db: AsyncSession, job_id: EntityId, status: JobStatus) -> datetime | None:
    """When this job *first* reached a status, from its own event log.

    First rather than last, deliberately. A job that failed and was reassigned
    enters `assigned` twice, and the cabinet's pipeline dates the stage the
    customer's order passed through — the moment a machine was first chosen for
    it. The reprint is reported separately, by `attempt`.

    ``None`` for a status never reached, which is exactly what an unlit stage on
    the pipeline means.

    The timestamp is the *database's*, because `JobEvent.created_at` is a server
    default — the same split `Session.expires_at` documents, where the injected
    clock and the server clock are deliberately different sources. For a stage
    label read by a person the two are indistinguishable.
    """
    when: datetime | None = await db.scalar(
        select(JobEvent.created_at)
        .where(JobEvent.job_id == job_id, JobEvent.to_status == status.value)
        .order_by(JobEvent.created_at)
        .limit(1)
    )
    return when
