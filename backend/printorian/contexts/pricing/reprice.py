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

* the **customer tier** — the loyalty discount is resolved from what the customer
  had spent at checkout and is never stored, so re-deriving it today would apply a
  different discount, and omitting it would overstate the prepared cost by the
  whole of it;
* the **per-line quote** — `OrderingService.place` prices the order and then
  *apportions* the total across lines by quantity, so `line_total` on a multi-line
  order is a share and not a price.

A difference cancels everything the two prices have in common: the tier, the
shipping choice, procurement, the finishes, the purge the AMS charges for extra
colours. What survives is what actually changed — print minutes and filament
grams — carried onto whatever the line was really quoted. The residual error is a
percentage of the *delta* rather than of the total, and it is in the direction
that holds a job for a person rather than the direction that dispatches
underpriced work.

Pure, like everything else in this package: rates are given, nothing is looked up.
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
