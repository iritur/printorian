"""Writing down what the planner decided.

The planner is pure (ARCHITECTURE §6) — it returns a `Plan` and touches nothing.
Turning that into rows, job state and events is this module's job, kept separate
from the job lifecycle so neither has to know how the other stores things.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PreparedPlate
from printorian.contexts.fleet import JobRequirements
from printorian.contexts.production import events as job_events
from printorian.contexts.production.journal import record_event
from printorian.contexts.production.models import AssignmentRecord, PrintJob, WaitListEntry
from printorian.contexts.production.policies import (
    DISPATCH_NO_PLATE,
    DISPATCH_NO_PRINTER,
    JobStatus,
    assert_transition,
)
from printorian.contexts.scheduling import AssignmentDecision, Plan, ReadyJob
from printorian.core.errors import PrintorianError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.storage import ObjectStore, StorageError
from printorian.drivers import PlateUpload, PrinterDriver


class PlateUnavailableError(PrintorianError):
    """There are no plate bytes to send.

    Distinct from a driver failure: nothing is wrong with the machine, the work
    simply is not ready. The dispatcher records it and re-queues rather than
    marking the printer unhealthy.
    """

    code = "error.production.plate_unavailable"


#: Most jobs one planning pass will consider. The planner is O(jobs × printers),
#: and a pass that took every ready job in a backed-up farm would hold the planning
#: lock for the whole of it. What is left over is picked up moments later by the
#: next pass, so the bound costs latency on a stalled farm and nothing otherwise.
PLAN_BATCH_SIZE = 500

#: Key for the advisory lock that makes planning single-flight. An arbitrary
#: constant — what matters is that every process uses the same one. PostgreSQL
#: namespaces advisory locks per database, so it cannot collide with anything
#: outside Printorian.
PLANNING_LOCK_KEY = 0x7072_6E74  # "prnt"


def to_ready_job(job: PrintJob) -> ReadyJob:
    """The planner's view of a stored job."""
    return ReadyJob(
        job_id=str(job.id),
        order_id=str(job.order_id),
        estimated_minutes=Decimal(job.estimated_minutes),
        due_at=job.due_at,
        priority=job.priority,
        requirements=JobRequirements(
            width_mm=Decimal(job.width_mm),
            depth_mm=Decimal(job.depth_mm),
            height_mm=Decimal(job.height_mm),
            material_type=job.material_type,
            colors=tuple(job.colors or ()),
            nozzle_diameter_mm=job.nozzle_diameter_mm,
            grams_required=Decimal(job.grams_required),
        ),
    )


async def plate_for(db: AsyncSession, store: ObjectStore, job: PrintJob) -> PlateUpload:
    """The plate to upload, with the bytes an engineer produced.

    Reads the file from the object store by the plate's content address. Raises
    rather than returning an empty upload when there is nothing to send: a printer
    given a zero-byte plate accepts it, starts, and produces nothing, which is the
    failure ADR-0007 exists to prevent. The dispatcher turns this into a recorded
    reason and puts the job back in the queue.
    """
    if job.prepared_plate_id is None:
        raise PlateUnavailableError("error.production.no_plate", job_id=str(job.id))

    plate = await db.get(PreparedPlate, job.prepared_plate_id)
    if plate is None or not plate.has_content:
        # A plate row can exist with numbers typed in and no file uploaded yet.
        # That is a legitimate state — an engineer recording what a slice produced
        # before sending the file — and it is not dispatchable.
        raise PlateUnavailableError("error.production.plate_has_no_file", job_id=str(job.id))

    content = await store.get(str(plate.content_sha256))
    return PlateUpload(
        filename=plate.filename or job.plate_filename or "plate.3mf", content=content
    )


def candidate_payload(decision: AssignmentDecision) -> list[dict[str, Any]]:
    """Every machine considered, whole.

    Stored as one document rather than normalised rows: it is an immutable record
    of a single moment, never queried field by field, and a schema change to the
    score components must not invalidate decisions already made.
    """
    return [
        {
            "printer_id": candidate.printer_id,
            "eligible": candidate.eligible,
            "reasons": list(candidate.reasons),
            "score": str(candidate.score),
            "components": [
                {
                    "code": component.code,
                    "value": str(component.value),
                    "weight": str(component.weight),
                }
                for component in candidate.components
            ],
        }
        for candidate in decision.candidates
    ]


async def persist_plan(
    db: AsyncSession, bus: EventBus, result: Plan, by_id: dict[str, PrintJob]
) -> None:
    """Record the decisions, move the assigned jobs, and refresh the wait list."""
    for decision in result.decisions:
        job = by_id[decision.job_id]
        chosen = next(
            (c for c in decision.candidates if c.printer_id == decision.chosen_printer_id),
            None,
        )
        # Written for every job, assigned or not. Keeping only the winners would
        # leave "why was my job *not* scheduled" unanswerable.
        db.add(
            AssignmentRecord(
                job_id=job.id,
                # The planner speaks in strings — it is pure and has no notion of an
                # `EntityId`. The column is a real UUID rather than the text it used
                # to be, so the boundary back into the database is where the id
                # becomes one again.
                chosen_printer_id=(
                    EntityId(decision.chosen_printer_id) if decision.chosen_printer_id else None
                ),
                winning_score=chosen.score if chosen else None,
                candidates=candidate_payload(decision),
            )
        )

    for assignment in result.assignments:
        job = by_id[assignment.job_id]
        assert_transition(job.status, JobStatus.ASSIGNED)
        previous = job.status
        job.status = JobStatus.ASSIGNED
        job.printer_id = EntityId(assignment.printer_id)
        await db.flush()
        await record_event(
            db,
            job,
            JobStatus.ASSIGNED,
            reason="scheduler.assigned",
            previous=previous,
            details={"printer_id": assignment.printer_id, "score": str(assignment.score)},
        )
        await bus.publish(
            job_events.JobAssigned(job_id=job.id, order_id=job.order_id, printer_id=job.printer_id)
        )

    await _refresh_wait_list(db, bus, result, by_id)


async def _refresh_wait_list(
    db: AsyncSession, bus: EventBus, result: Plan, by_id: dict[str, PrintJob]
) -> None:
    """Replace each waiting job's row rather than appending to it.

    A job is either waiting or it is not. Appending would let the cabinet show a
    customer a stale reason beside a current one.
    """
    for entry in result.wait_list:
        existing = await db.scalar(
            select(WaitListEntry).where(WaitListEntry.job_id == by_id[entry.job_id].id)
        )
        if existing is not None:
            await db.delete(existing)
    await db.flush()

    for entry in result.wait_list:
        job = by_id[entry.job_id]
        db.add(
            WaitListEntry(
                job_id=job.id,
                order_id=job.order_id,
                reason=entry.reason,
                predicted_start=entry.predicted_start,
                blocking_reasons=list(entry.blocking_reasons),
            )
        )
        await bus.publish(
            job_events.JobWaitListed(
                job_id=job.id,
                order_id=job.order_id,
                reason=entry.reason,
                predicted_start=(
                    entry.predicted_start.isoformat() if entry.predicted_start else ""
                ),
            )
        )
    await db.flush()


async def claim_ready_jobs(db: AsyncSession, *, limit: int = PLAN_BATCH_SIZE) -> list[PrintJob]:
    """Take the planning lock and the ready jobs, for the rest of this transaction.

    **Why any of this.** Planning reads the ready jobs, decides, and writes them
    back as assigned. Nothing stopped a second pass from doing that concurrently —
    and concurrent passes are not hypothetical, because ARCHITECTURE §6 requires an
    event-driven re-plan *as well as* the 30-second timer, so the two overlap by
    design. Two passes over one ready job assign it to two machines and dispatch it
    twice: two plates on two beds for one order.

    It had not happened only because a single worker process runs, which is an
    operational convention rather than an invariant. Two mechanisms make it one:

    * the advisory lock makes planning single-flight across every process on the
      database, so a second worker — or a restart overlap — waits rather than races;
    * ``FOR UPDATE SKIP LOCKED`` means that even if some future caller plans without
      taking the lock, no two transactions can hold the same job row.

    ``pg_advisory_xact_lock`` releases on commit *or* rollback, so there is no path
    where a crashed pass leaves the farm unable to plan — which both a lock table and
    a "planning" flag column would allow.

    SQLite has no advisory locks and needs none: the test dialect has one writer by
    construction, and its dialect renders ``FOR UPDATE`` as nothing at all.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PLANNING_LOCK_KEY})

    return list(
        await db.scalars(
            select(PrintJob)
            .where(PrintJob.status == JobStatus.READY)
            # The planner sorts properly by due-date risk; this ordering only decides
            # *which* jobs a bounded batch sees, and priority-then-oldest is the
            # honest answer to that.
            #
            # `id` last, because two terms still tie. Every job of one order is
            # written by a single intake pass, so they share `created_at` to the
            # last digit — it is `now()`, the transaction's clock — and at equal
            # priority the batch boundary then falls wherever the planner likes.
            # The job left outside it waits another pass for a reason nobody can
            # state, which is precisely what an assignment record exists to rule
            # out. The idiom and its one exception are in `core.pagination`.
            .order_by(PrintJob.priority.desc(), PrintJob.created_at, PrintJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


async def plate_to_send(
    db: AsyncSession,
    store: ObjectStore | None,
    job: PrintJob,
    driver: PrinterDriver | None,
) -> tuple[PlateUpload | None, str | None]:
    """Everything that must be true before a machine is touched.

    Returns the upload, or the code explaining why there is not one. Separated
    from `dispatch` so the pre-flight reads as a list of conditions rather than
    as five early returns interleaved with the happy path.

    Every failure here is a *re-queue*, not a printer fault: nothing has been
    placed on a bed and no material has been spent, so another machine can be
    tried and the reason is recorded either way (ADR-0007).
    """
    if job.printer_id is None:
        # Only reachable if an assignment was cleared between planning and
        # dispatch. Re-queuing is right — the planner will pick a machine again.
        return None, DISPATCH_NO_PRINTER
    if driver is None:
        # A machine with no driver is a machine a human drives. Saying so is
        # the honest outcome; inventing a dispatch is not.
        return None, "error.driver.unavailable"
    if store is None:
        # No object store configured, so no plate can be read. Refusing beats
        # sending an empty file to a printer that will happily accept it.
        return None, DISPATCH_NO_PLATE

    try:
        return await plate_for(db, store, job), None
    except (PlateUnavailableError, StorageError):
        # Distinct from an upload that failed: nothing was attempted, the
        # machine is fine, and the job waits for its plate rather than the
        # printer being blamed.
        return None, DISPATCH_NO_PLATE
