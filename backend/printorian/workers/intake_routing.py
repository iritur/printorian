"""Where a paid order goes once its jobs exist, and who attaches the plate.

Split out of `workers/intake.py` when that file reached the 400-line gate, and
split here rather than wherever the counter tripped: `intake.py` answers "which
paid orders have no jobs, and what jobs do they need", which is the question #41
was about and the one that runs whether or not the farm has a plate library. This
file answers the question #58 added — *the jobs exist; may the farm start them
itself?* — and it is the half that touches money, ADR-0006, ADR-0013 and ADR-0020
at once. The same seam `workers/runner.py` was cut along.

The three destinations, and the order between them is the part worth reading:

* **`PREP`** whenever any line still needs an engineer — a cache miss, a hit that
  could not be repriced honestly, or an order carrying a decision this pass has no
  measurement for (more than one line, or a plate whose recorded layout does not
  match what was ordered). It is the default, and it is exactly where every order
  went before #58.
* **`PRICE_REVIEW`** when nothing needs slicing but a plate came in over the
  ADR-0013 band. It is second rather than first on purpose: from `PRICE_REVIEW` an
  order may only go to `QUEUED`, so an order that also had unsliced work would be
  approved straight past it.
* **`QUEUED`** only when every line is attached and every variance is inside the
  band.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from printorian.contexts.ordering import OrderStatus
from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.production import JobStatus, JobView, ProductionService
from printorian.core.ids import EntityId
from printorian.workers.cached_plates import CachedPlates

logger = structlog.get_logger(__name__)


class OrderRouting:
    """Decides an order's next status, attaching what the farm already holds."""

    def __init__(
        self,
        production: ProductionService,
        cached: CachedPlates | None,
        *,
        tolerance: Decimal,
    ) -> None:
        self._production = production
        #: Given, the pass closes ADR-0006's loop itself. Withheld, every order
        #: goes to `PREP` exactly as it did before — which is what intake can
        #: honestly do without a plate library to ask and a configured band to
        #: judge against. `workers/passes.py` supplies both; a caller that does
        #: not is choosing the manual path, not silently losing money.
        self._cached = cached
        #: ADR-0013's band is configuration and never a constant here, so there is
        #: no default worth having: a tolerance of zero holds every plate that
        #: costs a rouble more than quoted, which is a visible, conservative
        #: failure rather than a silent generous one.
        self._tolerance = tolerance

    async def route(
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
        if cached is None or not self._may_attach_automatically(order, lines):
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

    def _may_attach_automatically(self, order: Order, lines: list[OrderLine]) -> bool:
        """Whether this order is one the farm may attach a plate to unattended.

        One condition, and it is about a number the order does not carry.
        `OrderingService.place` prices the order from `lines[0]` and then
        **apportions** the total across the lines by quantity, so on a multi-line
        order `line_total` is a share and was never a quote for that line's work.
        Feeding it to `assess_variance` would produce a variance against a number
        nobody was ever quoted — a measured-looking figure on the table ADR-0013
        exists to make trustworthy, which is the failure this whole path was
        written to avoid. Widening this means giving `ordering` a real per-line
        price, and that is a change to what an order *is*.

        **Quantity used to be the second condition here, and it was the wrong
        guard in the wrong place.** It refused a line of three against a plate of
        unknown layout, and then let a plate of three copies attach to a line of
        one — which is not the exotic case but the *normal* one, because a
        `PrintJob` is one plate holding a whole line's work, so the first order for
        three cubes is exactly what leaves a three-up plate in the library. The
        repeat order for one then took that whole bed as a single unit's work,
        landed inside the band anyway — the band is a percentage of the line total,
        and the extra work is a fraction of it — printed three and shipped one.

        Refusing the other direction as well would have been safe and would have
        turned the feature off, since the plate's layout was unknown either way.
        So the number was recorded instead: `PreparedPlate.copies`, nullable so an
        unrecorded layout stays unrecorded, checked against `line.quantity` in
        `workers/cached_plates.py` where both halves of the comparison are in hand.
        A line of three is now attachable — to a plate that says it holds three.
        """
        if len(lines) != 1:
            logger.info("intake.multi_line_order_not_repriceable", order_id=str(order.id))
            return False
        return True

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


__all__ = ["OrderRouting"]
