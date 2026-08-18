"""Attaching a prepared plate to a job, and what the truth costs.

Where ADR-0006 (cached slicer output) meets ADR-0013 (the quote is binding within
a band). Kept out of the service so the money rule can be read on its own.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production import events as job_events
from printorian.contexts.production.journal import record_event
from printorian.contexts.production.models import EstimateVariance, PrintJob
from printorian.contexts.production.policies import JobStatus, assert_transition
from printorian.contexts.production.prep import assess_variance
from printorian.core.events import EventBus
from printorian.core.ids import EntityId


async def attach_plate(
    db: AsyncSession,
    bus: EventBus,
    job: PrintJob,
    *,
    plate_id: EntityId,
    filename: str,
    print_minutes: Decimal,
    total_grams: Decimal,
    quoted_cost: Decimal,
    prepared_cost: Decimal,
    tolerance: Decimal,
) -> JobStatus:
    """Give a job its plate, and decide whether it may go on.

    Returns the status the job ended in: `READY` when the farm absorbs the
    difference, `ON_HOLD` when it does not. The variance is recorded either way —
    ADR-0013 wants all of them, because the ones inside the band are what
    calibrates the estimator.
    """
    verdict = assess_variance(
        quoted_cost=quoted_cost, prepared_cost=prepared_cost, tolerance=tolerance
    )

    db.add(
        EstimateVariance(
            job_id=job.id,
            order_id=job.order_id,
            quoted_cost=verdict.quoted_cost,
            prepared_cost=verdict.prepared_cost,
            tolerance=verdict.tolerance,
            within_tolerance=verdict.within_tolerance,
            estimated_minutes=job.estimated_minutes,
            prepared_minutes=print_minutes,
            estimated_grams=job.grams_required,
            prepared_grams=total_grams,
        )
    )

    previous = job.status
    job.prepared_plate_id = plate_id
    job.plate_filename = filename
    # The plate is the better truth, so the job now schedules against slicer
    # numbers rather than the mesh guess it was priced from.
    job.estimated_minutes = print_minutes
    job.grams_required = total_grams

    target = JobStatus.READY if verdict.within_tolerance else JobStatus.ON_HOLD
    assert_transition(previous, target)
    job.status = target
    await db.flush()

    await record_event(
        db,
        job,
        target,
        reason="plate.attached" if verdict.within_tolerance else "plate.variance_exceeded",
        previous=previous,
        details={
            "plate_id": str(plate_id),
            "quoted_cost": str(verdict.quoted_cost),
            "prepared_cost": str(verdict.prepared_cost),
            "overrun_ratio": str(verdict.ratio),
        },
    )

    if verdict.within_tolerance:
        await bus.publish(job_events.JobReady(job_id=job.id, order_id=job.order_id))
    else:
        # The order machine owns `PriceReview`; production says what it found and
        # lets ordering decide, rather than reaching across the boundary.
        await bus.publish(
            job_events.PlateVarianceExceeded(
                job_id=job.id,
                order_id=job.order_id,
                quoted_cost=str(verdict.quoted_cost),
                prepared_cost=str(verdict.prepared_cost),
                overrun_ratio=str(verdict.ratio),
            )
        )
    return target
