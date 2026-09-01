"""Attaching a prepared plate to a job, and what the truth costs.

Where ADR-0006 (cached slicer output) meets ADR-0013 (the quote is binding within
a band). Kept out of the service so the money rule can be read on its own.

**Both costs arrive as arguments and neither is computed here**, which is not
squeamishness about arithmetic: production owns what to do about a difference,
pricing owns what a thing costs, and this file is the first of those. The caller
that used to supply them was always a person at the console; since #58 it is also
`workers/intake.py`, which derives `prepared_cost` from the plate's own minutes
and grams under the order's pinned rates (`pricing.reprice`). Whichever caller it
is, the rule below is the same, and the one thing it must never be handed is a
number nobody measured — a zero or the quote copied across records "the estimate
was perfect" on the table this whole mechanism exists to make trustworthy.
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
        # **No rubles here.** A `JobEvent`'s details ride out on `JobView.events`,
        # which `GET /jobs/{job_id}` serves under `VIEW_PRODUCTION` alone — the
        # permission an operator on the floor holds. The two costs live on the
        # `EstimateVariance` row written above, and `GET /jobs/variances` gates
        # that on `VIEW_FINANCIALS` for exactly the reason CLAUDE.md §1 gives: a
        # response about seconds must not quietly start carrying money. Putting
        # them here too would be a second, ungated copy of the same two figures.
        #
        # It was harmless only by accident until #58: the sole caller was the
        # console's plate upload, whose costs are query parameters the console
        # never sends, so the field read `"0"`. The intake sweep now supplies the
        # order's real total and its repriced one, which is what turned a latent
        # leak into a live one, and which is why this branch is the one that
        # drops them.
        #
        # `overrun_ratio` stays. It is dimensionless, it is what the floor needs
        # to read an `ON_HOLD` against, and no amount of it recovers a rouble
        # figure without one of the two costs to scale it by — which is the thing
        # a caller without `VIEW_FINANCIALS` cannot get.
        details={"plate_id": str(plate_id), "overrun_ratio": str(verdict.ratio)},
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
