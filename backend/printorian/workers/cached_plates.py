"""Whether a paid line's plate is already in the library, and what it costs.

ADR-0006's payoff is that the *second* order of a configuration needs no human.
Until now the farm got half of it: `GET /jobs/plates/find` told an engineer the
work was already done, and the engineer still had to click. Closing the gap needs
one number nothing in the system produced — `EstimateVariance.prepared_cost` —
and this is where it is produced.

**It lives in `workers/` because it composes four contexts**, and a context that
reached across into three others would stop being a context. `catalog` says
whether the plate exists, `ordering` holds the rates that order was sold under,
`inventory` says what the filament costs, `pricing` turns those into money. The
same reasoning `workers/intake.py` gives for owning the order/job composition:
the caller composes, the contexts do not.

Every way this can decline to answer returns `None` and is logged with its own
code, and none of them guesses. A line whose plate is missing, whose order pinned
no rates, whose stored rates no longer rebuild to their own hash, whose material
has left the catalogue, or whose plate carries no minutes — each of those is a
job for an engineer, and each would otherwise be a fabricated variance on the one
table ADR-0013 exists to make trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary, PreparedPlateView
from printorian.contexts.inventory import InventoryService
from printorian.contexts.ordering import DeliveryMethod, rate_snapshot_for
from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.pricing import (
    FINISH_CATALOGUE,
    FinishOption,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    prepared_cost,
    rates_from_dict,
)
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.units import Duration, Mass

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PricedPlate:
    """A plate the farm already holds, and what this line's work reprices at."""

    plate: PreparedPlateView
    #: Derived from the plate's own minutes and grams under the order's pinned
    #: rates — never a constant, and never the quote copied across.
    prepared_cost: Decimal


class CachedPlates:
    """Answers whether a line is already sliced, and what that truth costs."""

    def __init__(self, db: AsyncSession, plates: PlateLibrary) -> None:
        self._db = db
        self._plates = plates

    async def for_line(
        self, order: Order, line: OrderLine, *, model_hash: str
    ) -> PricedPlate | None:
        """The cached plate for ``line``, priced, or nothing at all.

        ``model_hash`` is passed in rather than read here: the sweep already reads
        every line's digest in one query, and this is on the path of every paid
        order.
        """
        if not model_hash:
            # A line with no geometry on file — the manual order desk — can never
            # hit the cache: `plate_key` is built on the digest. Not an error, and
            # not worth a log line on every sweep.
            return None

        plate = await self._plates.find_unambiguous(
            model_hash=model_hash,
            scale=line.scale,
            material_code=line.material_code,
        )
        if plate is None:
            return None

        rates = await self._rates_for(order)
        if rates is None:
            return None

        spec = await self._quoted_spec(order, line)
        if spec is None:
            return None

        try:
            cost = prepared_cost(
                quoted=spec,
                quoted_cost=line.line_total,
                rates=rates,
                print_minutes=plate.print_minutes,
                total_grams=plate.total_grams,
            )
        except (ValidationError, DivisionByZero, InvalidOperation):
            # A plate with no minutes or no grams is numbers somebody has not
            # finished typing in. Pricing against it would record a perfect
            # estimate for a plate that claims to print in no time.
            logger.warning(
                "intake.plate_not_priceable",
                order_id=str(order.id),
                line_id=str(line.id),
                plate_id=str(plate.id),
            )
            return None

        return PricedPlate(plate=plate, prepared_cost=cost)

    async def _rates_for(self, order: Order) -> RateSnapshot | None:
        """The rates this order was sold under, rebuilt and checked against its id.

        `ordering.snapshots` is explicit that `rates_from_dict` is the wrong tool
        for *serving* a stored snapshot: it skips fields the row does not carry and
        `RateSnapshot` then fills them with today's defaults, so an old row would
        come back holding numbers that were never in force. Repricing has no other
        tool — the engine needs a `RateSnapshot`, not a dict — so the check is done
        here rather than avoided: the id **is** the content hash of the values, so a
        rebuilt snapshot whose hash does not match the row it came from was
        completed from today's defaults, and is refused.

        That is the whole guard against ADR-0020 being quietly undone. Without it
        this path would reprice a two-year-old order at whichever rates a later
        release happened to add, and nothing would say so.
        """
        try:
            record = await rate_snapshot_for(self._db, order.id)
        except NotFoundError:
            # Orders placed before ADR-0020 pinned nothing, and there is no honest
            # way to price their plate: the rates they were sold under were never
            # written down.
            logger.warning("intake.rates_not_pinned", order_id=str(order.id))
            return None

        rates = rates_from_dict(record.payload)
        if rates.snapshot_id != record.id:
            logger.warning(
                "intake.rates_not_reproducible",
                order_id=str(order.id),
                stored=record.id,
                rebuilt=rates.snapshot_id,
            )
            return None
        return rates

    async def _quoted_spec(self, order: Order, line: OrderLine) -> PriceSpec | None:
        """The pricing question this line was quoted from, rebuilt.

        Assembled the same way `api/routers/_line_pricing.spec_for` assembles it
        for the checkout, from the same catalogue of finishes — which is why
        `FINISH_CATALOGUE` moved into `pricing` rather than being copied here. A
        second, subtly different spec assembly is how a checkout quotes one number
        and an order charges another.

        The estimate is the *mesh* one the line recorded, not the plate's: this is
        the "before" of the comparison, and `pricing.reprice` supplies the "after".
        """
        try:
            material = await InventoryService(self._db).get_by_code(line.material_code)
        except NotFoundError:
            # The product has left the catalogue since the order was placed. Its
            # price per gram is gone, and inventing one would put a made-up figure
            # on both sides of the variance.
            logger.warning(
                "intake.material_not_in_catalogue",
                order_id=str(order.id),
                material_code=line.material_code,
            )
            return None

        try:
            return PriceSpec(
                estimate=PrintEstimate(
                    print_time=Duration(line.estimated_minutes),
                    material_mass=Mass(line.estimated_grams),
                ),
                material=MaterialPrice(
                    spec_code=material.code, price_per_gram=material.sell_price_per_gram
                ),
                quantity=line.quantity,
                colors=tuple(line.colors) if line.colors else ("default",),
                scale=line.scale,
                finishes=tuple(
                    FINISH_CATALOGUE.get(code, FinishOption(code=code)) for code in line.finishes
                ),
                rush=line.rush,
                # Collection is the absence of a shipping line rather than a zero
                # one, and the difference reaches the percentages applied over it.
                include_shipping=DeliveryMethod(order.delivery_method).is_shipped,
            )
        except (ValidationError, ValueError):
            # A line quoted with no minutes or no grams — orders from before the
            # estimator recorded them. There is nothing to compare a plate against.
            logger.warning(
                "intake.line_not_repriceable", order_id=str(order.id), line_id=str(line.id)
            )
            return None


__all__ = ["CachedPlates", "PricedPlate"]
