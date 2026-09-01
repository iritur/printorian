"""Turning a paid order into the print jobs that will make it.

Until this pass existed, nothing outside tests created a `PrintJob`. An order was
paid, `payments` moved it to `PAID`, and there it stopped — a person had to notice
and act, which is the one thing ROADMAP Phase 4's exit criterion says must not be
true. `tests/scenarios/test_repeat_order_skips_prep.py` carried a helper whose
docstring read "what a caller does when an order is paid"; this is that caller,
promoted out of the test suite and into the product.

**Reconciling rather than reactive**, for the reason `workers/postproduction.py`
argues at length: an `OrderStatusChanged` delivered while this process was
restarting is gone, and the order it was about would sit paid and jobless with
nobody looking for it. This pass asks "which paid orders have no jobs yet" and
makes the missing ones, so a missed tick costs latency and never an order.

It composes two contexts — `ordering` owns the order and its lines, `production`
owns the job — which is why it lives here and not in either of them.

**A cache hit now skips prep entirely.** This pass used to send every order to
`PREP`, sliced before or not, because attaching a plate writes an
`EstimateVariance` whose `prepared_cost` is `NOT NULL` (ADR-0013) and nothing in
the system priced a plate; a zero there, or the quote copied across, would have
recorded "the estimate was perfect" for a variance nobody measured.
`workers/cached_plates.py` is the answer to that — the plate's own minutes and
grams, repriced under the order's *own* pinned rates (ADR-0020) — and with a real
`prepared_cost` in hand a paid order can do what ROADMAP Phase 4 asks: payment to
`QUEUED`, no human anywhere in it.

Which of the three destinations an order actually reaches, and why the order
between them matters, is `workers/intake_routing.py`. This file stops at "the jobs
exist"; that one decides whether the farm may start them itself.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog.models import ModelAsset
from printorian.contexts.ordering import OrderingService, OrderStatus
from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.production import CreateJob, ProductionService
from printorian.contexts.production.models import PrintJob
from printorian.core.errors import DomainRuleViolationError, PrintorianError
from printorian.core.geometry import scaled_box
from printorian.core.ids import EntityId
from printorian.workers.cached_plates import CachedPlates
from printorian.workers.intake_routing import OrderRouting

logger = structlog.get_logger(__name__)

#: Most orders one pass will convert. A larger backlog gets the rest moments later.
RAISE_BATCH = 100


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """What one pass did, for logging and the health endpoint."""

    #: Orders that had no jobs and now do.
    raised: int = 0
    #: Jobs created across those orders — one per line.
    jobs: int = 0
    #: Orders that could not be converted. The rest of the pass still ran, and a
    #: failed order stays `PAID` so the next pass tries it again.
    failed: int = 0


class IntakeSweep:
    """One reconciling pass over paid orders that have no jobs."""

    def __init__(
        self,
        db: AsyncSession,
        production: ProductionService,
        ordering: OrderingService,
        cached: CachedPlates | None = None,
        *,
        tolerance: Decimal = Decimal(0),
    ):
        self._db = db
        self._production = production
        self._ordering = ordering
        #: `cached` and `tolerance` are this pass's arguments rather than the
        #: routing's, because they are what a *caller* chooses — `workers/passes.py`
        #: supplies both, and a caller that supplies neither is choosing the manual
        #: path. What they mean once chosen is argued in `intake_routing.py`.
        self._routing = OrderRouting(production, cached, tolerance=tolerance)

    async def sweep(self) -> SweepOutcome:
        raised = jobs = failed = 0
        for order_id in await self._paid_orders():
            try:
                made = await self._raise_for(order_id)
            except PrintorianError as exc:
                # One order that cannot be converted must not stop the rest, and
                # must not be silently dropped: it keeps its `PAID` status, so the
                # next pass finds it again by the same query.
                failed += 1
                logger.warning("intake.raise_failed", order_id=str(order_id), code=exc.code)
                continue
            if made:
                raised += 1
                jobs += made
        return SweepOutcome(raised=raised, jobs=jobs, failed=failed)

    async def _paid_orders(self) -> list[EntityId]:
        """Paid orders with no job against them yet.

        `NOT EXISTS` rather than reading every job's `order_id` into a set: the
        set grows with production history for ever, while this stays a lookup on
        `ix_print_jobs_order_id` per candidate.

        Ordered by `paid_at` *and* `id`. The tie-break is not decoration — a
        timestamp alone is not a total order, two orders paid in the same
        millisecond make the batch boundary arbitrary, and issue #42 is about the
        fifteen queries already carrying that flake class. Not sixteen.
        """
        query = (
            select(Order.id)
            .where(
                Order.status == OrderStatus.PAID,
                ~select(PrintJob.id).where(PrintJob.order_id == Order.id).exists(),
            )
            .order_by(Order.paid_at, Order.id)
            .limit(RAISE_BATCH)
        )
        return list(await self._db.scalars(query))

    async def _raise_for(self, order_id: EntityId) -> int:
        """Make one job per line, then move the order on to wherever it belongs.

        Where that is, is `intake_routing.py`'s answer — `PREP` unless the farm
        can attach and price every plate itself.

        Returns how many jobs were made. Zero means the order was skipped rather
        than converted, and it keeps its status so somebody can find it.
        """
        order = await self._db.get(Order, order_id)
        if order is None:  # pragma: no cover — the query above just read it
            return 0

        lines = list(
            await self._db.scalars(select(OrderLine).where(OrderLine.order_id == order.id))
        )
        if not lines:
            # A paid order with nothing in it is a defect upstream, not something
            # to paper over by advancing it to prep with no work attached. Left
            # `PAID` and logged, which is where a person will look for it.
            logger.warning("intake.order_has_no_lines", order_id=str(order.id))
            return 0

        hashes = await self._model_hashes(lines)
        jobs = [
            await self._production.create_job(self._job_for(line, order, hashes)) for line in lines
        ]

        target = await self._routing.route(order, lines, jobs, hashes)
        await self._ordering.advance(order.id, target, reason="order.intake")
        return len(lines)

    async def _model_hashes(self, lines: list[OrderLine]) -> dict[EntityId, str]:
        """The content digest of every asset these lines were priced from.

        One query for the whole order rather than one per line: an order of twenty
        lines is one round trip, and the digests are what the plate cache is keyed
        on, so this is on the path of every order the farm takes.
        """
        wanted = {line.model_asset_id for line in lines if line.model_asset_id is not None}
        if not wanted:
            return {}
        rows = await self._db.execute(
            select(ModelAsset.id, ModelAsset.sha256).where(ModelAsset.id.in_(wanted))
        )
        return dict(rows.tuples().all())

    def _job_for(self, line: OrderLine, order: Order, hashes: dict[EntityId, str]) -> CreateJob:
        """One line's work, in the terms production understands.

        The guard is the whole point of this method. `model_hash` is what
        `plate_key` is built from, so a job that carries an asset but no digest is
        one the plate cache can never hit — it would slice correctly, print
        correctly, and quietly send every repeat of that configuration back
        through an engineer for ever. That is the silent, flattering shape of this
        bug, so it refuses the order rather than creating the job.
        """
        model_hash = hashes.get(line.model_asset_id, "") if line.model_asset_id else ""
        if line.model_asset_id is not None and not model_hash:
            raise DomainRuleViolationError(
                "error.intake.model_hash_missing",
                order_id=str(order.id),
                line_id=str(line.id),
                model_asset_id=str(line.model_asset_id),
            )

        return CreateJob(
            order_id=order.id,
            # Carried through, never re-derived: the line records what was priced,
            # and a job priced from one mesh and printed from another is the
            # failure `OrderLine.model_asset_id`'s comment is written about.
            model_asset_id=line.model_asset_id,
            model_hash=model_hash,
            scale=line.scale,
            material_type=line.material_code,
            colors=list(line.colors),
            **_dimensions(line),
            # Per unit on the line, so the job carries the whole line's work — the
            # same reading `workers/packaging.py` takes of these columns. One job
            # per line rather than per unit because a `PrintJob` is one plate, and
            # how many copies fit on a plate is what the engineer decides at prep.
            grams_required=line.estimated_grams * line.quantity,
            estimated_minutes=line.estimated_minutes * line.quantity,
            due_at=order.promised_at,
            # Rush is the customer paying for the front of the queue, so it has to
            # reach the planner as priority or it was sold and not delivered.
            priority=1 if line.rush else 0,
        )


def _dimensions(line: OrderLine) -> dict[str, Decimal]:
    """The part's bounding box, at the size it was ordered.

    **At the size it was ordered** is the correction worth reading. The box stored
    on the line is the box of the *unscaled* mesh — `_pricing_spec` writes
    `analysis.bounding_box` verbatim while `estimate()` applies the scale only to
    volume, mass and time — so this used to hand the planner a 100 mm part for a
    100 mm mesh ordered at scale 3. `fleet.can_take`'s only geometric test is
    `job.width_mm > printer.width_mm`, so every printer in the farm was judged
    against a third of the real part. `core.geometry.scaled_box` is that
    multiplication, shared with `workers/packaging`, which was reading the same
    unscaled box for the shipping carton.

    Zero for geometry nobody measured, which is what the column already defaults
    to and what the planner reads as "no constraint" — an *invented* box would
    have the planner refuse machines that would have fitted it, or accept one that
    would not. That default is safe only because a person releases the job:
    `workers/plate_admission` refuses the unattended path outright rather than
    letting a zero read as "fits every machine".
    """
    box = scaled_box(line.mesh, line.scale)
    if box is None:
        return {"width_mm": Decimal(0), "depth_mm": Decimal(0), "height_mm": Decimal(0)}
    return {"width_mm": box.x, "depth_mm": box.y, "height_mm": box.z}


async def run_forever(
    build_sweep: Any,
    *,
    interval_seconds: int,
    stop: asyncio.Event | None = None,
) -> None:
    """Sweep on an interval until stopped.

    `build_sweep` returns a fresh sweep per pass, each with its own session and
    its own commit — the arrangement every loop here uses, and for the reason
    `workers/passes.py` gives: a session held across hours reads a snapshot older
    than the orders it is meant to notice.
    """
    stop = stop or asyncio.Event()

    while not stop.is_set():
        try:
            sweep = await build_sweep()
            outcome = await sweep.sweep()
            if outcome.raised or outcome.failed:
                logger.info(
                    "intake_sweep",
                    raised=outcome.raised,
                    jobs=outcome.jobs,
                    failed=outcome.failed,
                )
        except Exception:
            # A bad pass is logged and the loop continues: one failure must not
            # leave every paid order invisible to production for ever after.
            logger.exception("intake_sweep_failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


__all__ = ["RAISE_BATCH", "IntakeSweep", "SweepOutcome", "run_forever"]
