"""What a line would have been quoted for, had the slicer numbers been known.

ADR-0013 makes the quote binding within a band, and the band is judged on money:
`EstimateVariance` holds a `quoted_cost` and a `prepared_cost`, both `NOT NULL`.
The quoted half has always been available. The prepared half had no source at
all — nothing in the system prices a plate, because pricing happens once, at
quote time, from the mesh heuristic — and so every plate the farm attached
automatically would have had to record a fabricated number on the one table
ADR-0013 exists to make trustworthy.

This is that source, and it is deliberately a **difference rather than a fresh
price**::

    prepared_cost = quoted_cost + (price(with the plate's numbers)
                                   - price(with the numbers that were quoted))

both priced under the order's *own* pinned `RateSnapshot` (ADR-0020), which is
kept precisely so work already sold can be re-derived without being re-rated.

**Why not simply price the line again and take the total.** Two inputs of the
original quote are not recoverable from the order, and both would land on the
money column:

* the **customer tier** — resolved from what the customer had spent at checkout,
  and held by no column on the order; only its *effect* survives, as a rendered
  discount line inside `price_breakdown`, and rebuilding a tier out of that is a
  second implementation of the loyalty ladder. Re-deriving it from today's spend
  would apply a different discount, and omitting it would overstate the prepared
  cost by the whole of it;
* the **per-line quote** — `OrderingService.place` prices the order and then
  *apportions* the total across lines by quantity, so `line_total` on a multi-line
  order is a share and not a price.

A difference cancels everything the two prices have in common: the tier, the
shipping choice, procurement, the finishes, the purge the AMS charges for extra
colours. What survives is what actually changed — print minutes and filament
grams — carried onto whatever the line was really quoted. The residual error is a
percentage of the *delta* rather than of the total.

**It is not conservative in every direction, and this is the exception.** Pricing
the delta without the tier understates it whenever the tier's
`margin_percent_override` is *above* the snapshot's `margin_percent` — the change
is marked up at 30% where the customer's book says 45 — so an overrun that the
band should have caught can land inside it. Every other direction overstates the
delta and holds the job for a person, which is the safe way round.
`test_reprice_tier.py` pins the size of that gap. Reconstructing the tier from the
stored `price_breakdown` would close it: the applied percents really are on
`Basis.percent` and `breakdown_from_dict` reads them back. What it would not
recover is the per-line apportionment, which does not exist on the order at all —
and that is the argument for the difference, not the tier.

Pure, like everything else in this package: rates are given, nothing is looked up.
The one input that is *neither* given here nor pinned by ADR-0020 is the material's
price per gram: `workers/cached_plates.py` reads it live from `inventory` for both
sides of the difference. See that module for what that costs.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing.delta import preview
from printorian.contexts.pricing.rates import RateSnapshot
from printorian.contexts.pricing.spec import EstimateSource, PriceSpec, PrintEstimate
from printorian.core.units import Duration, Mass


def prepared_cost(
    *,
    quoted: PriceSpec,
    quoted_cost: Decimal,
    rates: RateSnapshot,
    print_minutes: Decimal,
    total_grams: Decimal,
) -> Decimal:
    """Price ``quoted``'s line again with the plate's numbers, and anchor it.

    ``print_minutes`` and ``total_grams`` are the *plate's* totals, because a
    `PrintJob` is one plate and one line's whole work — that is already how
    `production.plates.attach_plate` reads them when it overwrites
    `estimated_minutes` and `grams_required`. The engine wants per-unit figures
    and multiplies by quantity, so they are divided here rather than at the call
    site: dividing in two places is how the two would eventually disagree.

    **That division is a claim the caller has to have earned.** Dividing by
    ``quoted.quantity`` says the plate holds that many copies. Hand this a one-up
    plate for a line of three and it reprices at a third of the work; hand it a
    three-up plate for a line of one and it reprices at three times — both inside
    the band for the small parts this farm mostly makes, both flattering, both on
    the table ADR-0013 exists to make trustworthy. The automatic caller
    (`workers/cached_plates.py`) therefore attaches only a plate whose recorded
    `copies` equals the line's quantity, and refuses a plate that does not say.

    Raises `ValidationError` (`error.pricing.print_time` / `error.pricing.material_mass`)
    when the plate carries no minutes or no grams. That is not a plate to price
    against — a zero there would make the estimate look perfect — and the caller
    is expected to leave the job for an engineer rather than record a variance
    nobody measured.
    """
    units = Decimal(quoted.quantity)
    truth = PrintEstimate(
        print_time=Duration(print_minutes / units),
        material_mass=Mass(total_grams / units),
        # Recorded on the estimate rather than inferred later: ADR-0013's whole
        # progression is mesh heuristic -> prepared plate -> measured, and a
        # breakdown that cannot say which one it was built from is a number
        # without a provenance.
        source=EstimateSource.PREPARED_PLATE,
    )
    return quoted_cost + preview(quoted, rates, estimate=truth).total_change.amount


__all__ = ["prepared_cost"]
