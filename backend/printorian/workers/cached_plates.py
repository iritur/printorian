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
code, and none of them guesses. A line whose plate is missing or ambiguous, whose
order pinned no rates, whose stored payload will not rebuild at all, whose rebuilt
rates no longer hash to their own row, whose material has left the catalogue, or
whose plate carries no minutes — each of those is a job for an engineer, and each
would otherwise be a fabricated variance on the one table ADR-0013 exists to make
trustworthy.

The refusal about **layout** used to live in the caller and be one-sided, and that
was wrong in the expensive direction. `workers/intake_routing.py` declined any line
of more than one because nothing recorded how many copies a plate held — and then
attached a plate holding two copies to a line of one, which is the *normal* cache
entry here: one `PrintJob` is one plate, and `intake._job_for` sets the job's
minutes and grams to the line's per-unit figures times its quantity, so the first
order for two keychains leaves a two-up plate behind. The repeat order for one
then divided that whole bed by a quantity of one and priced it as a single unit's
work: 4.26% over the quote — inside ADR-0013's band — so it queued, printed two,
shipped one, and recorded the estimate as accurate.

`PreparedPlate.copies` is where the number lives now, and the refusal is here
rather than in the caller because both halves of the comparison are here: the
plate's recorded layout and the line's quantity must agree, and a plate whose
copies nobody wrote down is refused whatever the line says.
`pricing.reprice.prepared_cost` is what that division would otherwise assume.
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
    #: rates — never a constant, and never the quote copied across. "Pinned" is
    #: true of the `RateSnapshot` and not of the material's price per gram, which
    #: `_quoted_spec` reads live; that exception is argued there.
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
        plate = await self._usable_plate(order, line, model_hash=model_hash)
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

    async def _usable_plate(
        self, order: Order, line: OrderLine, *, model_hash: str
    ) -> PreparedPlateView | None:
        """The cached plate this line may be attached to, if the farm has one.

        Split from `for_line` above because the two questions are different sizes:
        this one is "does a plate exist that is *this line's* plate", and what
        follows it is "can that plate be priced without inventing anything". Every
        refusal here is about the plate; every refusal there is about the money.
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
        if plate.copies is None or plate.copies != line.quantity:
            # The plate's minutes and grams are the whole bed's. `attach_plate`
            # writes them onto the job as its total work and `prepared_cost`
            # divides them by `line.quantity` to get a per-unit figure, so both
            # steps are asserting how many parts are on that bed. Agreeing counts
            # is the only thing that makes either true.
            #
            # `None` — a plate recorded before this column existed, or by an
            # engineer who did not say — is refused rather than assumed to be one.
            # One is the value that makes the common case attach, so guessing it
            # would reinstate exactly the failure this guard exists for.
            logger.info(
                "intake.plate_layout_does_not_match_line",
                order_id=str(order.id),
                line_id=str(line.id),
                plate_id=str(plate.id),
                plate_copies=plate.copies,
                line_quantity=line.quantity,
            )
            return None
        return plate

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

        try:
            rates = rates_from_dict(record.payload)
        except (ValidationError, ValueError, TypeError, KeyError, ArithmeticError):
            # A payload the current `RateSnapshot` cannot be rebuilt from — a rate
            # whose *type* changed between releases is the way this arrives, and
            # it is the same family of drift the id check below is about.
            #
            # It is caught rather than allowed to propagate because of where this
            # runs: `IntakeSweep.sweep` only rescues `PrintorianError` per order,
            # so anything else escapes the whole pass and every *other* paid order
            # in the batch stops being converted too. One unpriceable order must
            # cost that order an engineer, not cost the farm its intake.
            logger.warning(
                "intake.rates_not_rebuildable", order_id=str(order.id), snapshot_id=record.id
            )
            return None
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

        **One input here is live, and it is the only one.** ADR-0020 pins the
        `RateSnapshot` and `_rates_for` above checks it hash for hash, but
        `sell_price_per_gram` is not in the snapshot — it is a mutable catalogue
        column, and it is read from today's `inventory` for *both* sides of the
        difference. So the residual is
        `(plate_grams - quoted_grams) x (price_today - price_when_quoted)`: bounded
        by the mass difference rather than by the total, and zero whenever the
        filament has not moved. It is nevertheless a live number entering a figure
        this branch's documentation otherwise describes as computed under the
        order's own pinned rates, so it is said here rather than left implied.
        Removing it means carrying the line's price per gram onto `OrderLine` at
        `place()` time — a schema change on the checkout path, not on this one.
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
