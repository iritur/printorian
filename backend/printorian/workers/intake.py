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
`prepared_cost` in hand this pass can do what ROADMAP Phase 4 asks: payment to
`QUEUED`, no human anywhere in it.

The three destinations, and the order between them is the part worth reading:

* **`PREP`** whenever any line still needs an engineer — a cache miss, or a hit
  that could not be repriced honestly. Unchanged, and it stays the default.
* **`PRICE_REVIEW`** when nothing needs slicing but a plate came in over the
  ADR-0013 band. It is second rather than first on purpose: from `PRICE_REVIEW`
  an order may only go to `QUEUED`, so an order that also had unsliced work would
  be approved straight past it.
* **`QUEUED`** only when every line is attached and every variance is inside the
  band.
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
from printorian.contexts.production import CreateJob, JobStatus, JobView, ProductionService
from printorian.contexts.production.models import PrintJob
from printorian.core.errors import DomainRuleViolationError, PrintorianError
from printorian.core.ids import EntityId
from printorian.workers.cached_plates import CachedPlates

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
        #: Given, the pass closes ADR-0006's loop itself. Withheld, every order
        #: goes to `PREP` exactly as it did before — which is what the sweep can
        #: honestly do without a plate library to ask and a configured band to
        #: judge against. `workers/passes.py` supplies both; a caller that does
        #: not is choosing the manual path, not silently losing money.
        self._cached = cached
        #: ADR-0013's band is configuration and never a constant here, so there is
        #: no default worth having: a tolerance of zero holds every plate that
        #: costs a rouble more than quoted, which is a visible, conservative
        #: failure rather than a silent generous one.
        self._tolerance = tolerance

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
        """Make one job per line, then move the order on to prep.

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

        target = await self._route(order, lines, jobs, hashes)
        await self._ordering.advance(order.id, target, reason="order.intake")
        return len(lines)

    async def _route(
        self,
        order: Order,
        lines: list[OrderLine],
        jobs: list[JobView],
        hashes: dict[EntityId, str],
    ) -> OrderStatus:
        """Where the order goes now that its jobs exist.

        The three destinations are argued in the module docstring; this is the
        arithmetic behind them. Note that a line whose plate could not be attached
        contributes a `PENDING` job, which is what puts the order in `PREP` — so
        every refusal inside `cached_plates` lands as "an engineer looks at it",
        never as "it went out anyway".
        """
        cached = self._cached
        if cached is None or not self._is_repriceable(order, lines):
            return OrderStatus.PREP

        statuses = [
            await self._attach_cached(cached, order, line, job, hashes)
            for line, job in zip(lines, jobs, strict=True)
        ]
        if any(status is JobStatus.PENDING for status in statuses):
            return OrderStatus.PREP
        if any(status is JobStatus.ON_HOLD for status in statuses):
            return OrderStatus.PRICE_REVIEW
        return OrderStatus.QUEUED

    def _is_repriceable(self, order: Order, lines: list[OrderLine]) -> bool:
        """Whether this order's lines carry a *price* to compare a plate against.

        Only a single-line order does. `OrderingService.place` prices the order
        from `lines[0]` and then **apportions** the total across the lines by
        quantity, so on a multi-line order `line_total` is a share and was never a
        quote for that line's work. Feeding it to `assess_variance` would produce a
        variance against a number nobody ever quoted — a measured-looking figure on
        the table ADR-0013 exists to make trustworthy, which is the failure this
        whole path was written to avoid.

        So a multi-line order takes exactly the route it took before: an engineer,
        who can see the whole order. Widening this means giving `ordering` a real
        per-line price, and that is a change to what an order *is*.
        """
        if len(lines) == 1:
            return True
        logger.info("intake.multi_line_order_not_repriceable", order_id=str(order.id))
        return False

    async def _attach_cached(
        self,
        cached: CachedPlates,
        order: Order,
        line: OrderLine,
        job: JobView,
        hashes: dict[EntityId, str],
    ) -> JobStatus:
        """Attach this line's cached plate, if the farm has one it can price.

        Returns the status the job is now in. `PENDING` means the automatic path
        declined — no plate, or nothing that could be priced without inventing a
        number — and the job is still an engineer's.
        """
        model_hash = hashes.get(line.model_asset_id, "") if line.model_asset_id else ""
        priced = await cached.for_line(order, line, model_hash=model_hash)
        if priced is None:
            return JobStatus.PENDING

        updated = await self._production.attach_prepared_plate(
            job.id,
            plate_id=priced.plate.id,
            filename=priced.plate.filename,
            print_minutes=priced.plate.print_minutes,
            total_grams=priced.plate.total_grams,
            # What the customer agreed to, against what the slicer says the work
            # is. `attach_prepared_plate` records both either way — ADR-0013 wants
            # the variances inside the band as much as the ones outside it, since
            # those are what calibrate the estimator.
            quoted_cost=line.line_total,
            prepared_cost=priced.prepared_cost,
            tolerance=self._tolerance,
        )
        return updated.status

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
    """The part's bounding box, from the geometry the line was priced on.

    Zero for geometry nobody measured, which is what the column already defaults
    to and what the planner reads as "no constraint" — an *invented* box would
    have the planner refuse machines that would have fitted it, or accept one that
    would not.
    """
    box: Any = line.mesh.get("bounding_box_mm") if isinstance(line.mesh, dict) else None
    if not isinstance(box, dict):
        return {"width_mm": Decimal(0), "depth_mm": Decimal(0), "height_mm": Decimal(0)}
    try:
        return {
            "width_mm": Decimal(str(box["x"])),
            "depth_mm": Decimal(str(box["y"])),
            "height_mm": Decimal(str(box["z"])),
        }
    except (KeyError, ArithmeticError, TypeError, ValueError):
        return {"width_mm": Decimal(0), "depth_mm": Decimal(0), "height_mm": Decimal(0)}


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
