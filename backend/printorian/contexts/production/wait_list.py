"""Taking rows off the wait list — the only place that happens.

Two callers, and they were one edit away from being two implementations. The
planner replaces a waiting job's row on every pass (`planning._refresh_wait_list`),
and the owner's «Очистить лист ожидания» drops every row there is. `drop-telemetry`
and the maintenance worker share `fleet.retention.drop_telemetry_past_retention`
for exactly this reason: two code paths to one irreversible act drift apart, and
the one that drifts is whichever is exercised less.

**Clearing does not move a job.** `design/settings.html` describes the button as
returning waiting orders to «Подготовка», and the job state machine refuses that:
`policies.TRANSITIONS` gives `READY` only `ASSIGNED` and `CANCELLED`, and the note
beside `ON_HOLD` says why nothing goes back to `PENDING` — the plate exists, and
re-slicing it would not be the fix. So this removes the *record of the wait*, which
is what the wait list is, and leaves the jobs exactly as ready as they were. The
kit's hint is a discrepancy to settle with a person, not a licence to write an
illegal transition.

**What is irreversible about it** is therefore not the queue — a still-blocked job
is written back onto the list by the next planning pass, seconds later. It is the
*reasons*: why each job was stuck, what was blocking it and when it was predicted
to start are held nowhere else, and a `DELETE` takes them with it. That is why the
farm-wide clear copies each row into the job's own journal before dropping it. An
audit that recorded only "the list was cleared" would answer none of the questions
the list was answering.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.journal import record_event
from printorian.contexts.production.models import PrintJob, WaitListEntry
from printorian.core.ids import EntityId

#: The journal reason written against every job whose wait was cleared by hand.
#: Distinct from the planner's own codes — «Очистить лист ожидания» is a person
#: deciding, and reading it back as a scheduling outcome would be wrong.
CLEARED_BY_HAND = "settings.wait_list_cleared"


async def discard(
    db: AsyncSession, *, job_ids: Collection[EntityId] | None = None
) -> list[WaitListEntry]:
    """Delete wait-list rows and return the ones that actually went.

    ``job_ids=None`` means every row; an empty collection means none, and is not
    the same request — the planner asks about a specific set of jobs and a pass
    that re-planned nothing must not empty the table.

    The rows are read and then deleted one at a time rather than issued as a bulk
    ``DELETE``, for two reasons. The caller needs the rows themselves, not a count,
    because the count is all a bulk statement could audit and the count is the part
    that says nothing. And the planner holds these entries in the session already,
    so a bulk statement would leave stale objects behind it in the identity map.
    """
    if job_ids is not None and not job_ids:
        return []

    query = select(WaitListEntry)
    if job_ids is not None:
        query = query.where(WaitListEntry.job_id.in_(job_ids))

    entries = list(await db.scalars(query))
    for entry in entries:
        await db.delete(entry)
    await db.flush()
    return entries


async def clear_wait_list(db: AsyncSession, *, by: EntityId | None) -> int:
    """Empty the wait list, writing each removed row into its job's journal first.

    The owner's irreversible operation, audited per row the way
    `SettingsService.reset_prefix` audits each override it drops. The journal is
    the right book rather than the settings audit: nothing in the settings
    catalogue changed, and inventing a «было · стало» line for a key that does not
    exist would put an edit that never happened in front of the next person
    reading that table.

    ``by`` travels in ``details`` because `JobEvent` has no actor column — unlike
    `OrderEvent`, which does. Adding one is a migration on the busiest history
    table in the schema for the sake of one caller, and the audit is answerable
    without it; if a second hand-driven job event ever appears, that is the moment
    to make it a column.

    The event carries the job's status on both sides, unchanged, because that is
    the truth: something was recorded about this job and the job did not move.
    """
    entries = await discard(db)
    if not entries:
        return 0

    jobs = {
        job.id: job
        for job in await db.scalars(
            select(PrintJob).where(PrintJob.id.in_([entry.job_id for entry in entries]))
        )
    }
    for entry in entries:
        job = jobs.get(entry.job_id)
        if job is None:
            # The job went between the two reads. Nothing to write it against, and
            # nothing to fabricate: a journal row needs a job to hang from.
            continue
        await record_event(
            db,
            job,
            job.status,
            reason=CLEARED_BY_HAND,
            previous=job.status,
            details={
                "wait_reason": entry.reason,
                "blocking_reasons": list(entry.blocking_reasons),
                # Null means the wait needed a person rather than time — see
                # `WaitListEntry.predicted_start`. Kept null rather than stamped
                # with a date nobody predicted.
                "predicted_start": (
                    entry.predicted_start.isoformat() if entry.predicted_start else None
                ),
                "cleared_by": str(by) if by is not None else None,
            },
        )
    return len(entries)


__all__ = ["CLEARED_BY_HAND", "clear_wait_list", "discard"]
