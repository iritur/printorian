"""The floor's read-only views of production.

Split from the service partly because it was over the file-length gate, and partly
because these two are the queries that most need bounding and least need the
service's clock, bus or transaction. Both were unbounded: `assignment_records` is
written for every job on every planning pass, so "the decisions for this job" grows
with how long the job waited, and the wait list grows with how badly the farm is
stalled — exactly when someone is most likely to be looking at it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import AssignmentRecord, WaitListEntry
from printorian.core.ids import EntityId

#: Most wait-list entries returned at once. A farm with more than this waiting has a
#: problem no screen can usefully render.
WAIT_LIST_LIMIT = 200

#: Most planning decisions returned for one job. Newest first, so the bound drops
#: the oldest — and "why is it there *now*" is the question being asked.
DECISION_LIMIT = 50


async def wait_list(db: AsyncSession, *, limit: int = WAIT_LIST_LIMIT) -> list[WaitListEntry]:
    """Jobs nothing can take yet.

    Entries with a predicted start come first: those are waiting on time, and time
    will fix them. The ones with no prediction need a person, and putting them last
    is not a demotion — it is where the eye lands after the queue that is moving.
    """
    return list(
        await db.scalars(
            select(WaitListEntry)
            .order_by(WaitListEntry.predicted_start.is_(None), WaitListEntry.predicted_start)
            .limit(limit)
        )
    )


async def decisions_for(
    db: AsyncSession, job_id: EntityId, *, limit: int = DECISION_LIMIT
) -> list[AssignmentRecord]:
    """Why this job went where it went — most recent planning pass first."""
    return list(
        await db.scalars(
            select(AssignmentRecord)
            .where(AssignmentRecord.job_id == job_id)
            .order_by(AssignmentRecord.created_at.desc())
            .limit(limit)
        )
    )


__all__ = ["DECISION_LIMIT", "WAIT_LIST_LIMIT", "decisions_for", "wait_list"]
