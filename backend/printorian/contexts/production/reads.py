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

    Ties are the common case rather than the edge one: the planner predicts starts
    from one pass's arithmetic, so a farm waiting on the same machine gets the same
    instant for every job behind it — and every entry with no prediction at all
    ties with every other. `id` gives the rest of the list a total order, which
    matters here because `limit` decides *membership*: without it the two hundredth
    row and the two hundred and first swap places between reads, and one of them is
    on the screen. See `core.pagination`.
    """
    return list(
        await db.scalars(
            select(WaitListEntry)
            .order_by(
                WaitListEntry.predicted_start.is_(None),
                WaitListEntry.predicted_start,
                WaitListEntry.id,
            )
            .limit(limit)
        )
    )


async def decisions_for(
    db: AsyncSession, job_id: EntityId, *, limit: int = DECISION_LIMIT
) -> list[AssignmentRecord]:
    """Why this job went where it went — most recent planning pass first.

    Ordered by `id` as well as time, and the second term is the point of the table
    rather than decoration. `created_at` is a `server_default` of `now()`, which in
    PostgreSQL is the *transaction's* start — so two decisions recorded by one pass,
    or by two passes inside one transaction, carry the identical timestamp and the
    sort ties. A record whose whole job is explaining the order things were
    considered in must not be able to answer differently twice; `id` is a UUIDv7
    from the real clock, so it stays chronological where the timestamp does not
    move. See `core.pagination` for the idiom.
    """
    return list(
        await db.scalars(
            select(AssignmentRecord)
            .where(AssignmentRecord.job_id == job_id)
            .order_by(AssignmentRecord.created_at.desc(), AssignmentRecord.id.desc())
            .limit(limit)
        )
    )


__all__ = ["DECISION_LIMIT", "WAIT_LIST_LIMIT", "decisions_for", "wait_list"]
