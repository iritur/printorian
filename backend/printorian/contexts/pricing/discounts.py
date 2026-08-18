"""Volume discounts, and the tier-cliff guard.

A plain step ladder has a cliff at every threshold. With a 5% tier at 10 and a 12%
tier at 50, ordering **50 units costs less than ordering 49** — so the farm prints
one more part, spends more material and machine time, and is paid less for it. The
customer is not being clever; the pricing rule is simply wrong at that point.

The guard caps the discount at each threshold so the order total never decreases as
quantity grows. The customer still gets a genuine volume discount; it is just never
so large that 50 undercuts 49.

Within a tier the total always rises with quantity (the base grows, the percentage
is constant), so checking each threshold against the unit below it is sufficient to
make the whole curve non-decreasing.

To go back to raw step tiers, set ``RateSnapshot.guard_tier_cliffs = False``. The
behaviour is a commercial choice, so it is configuration rather than a constant.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal

from printorian.contexts.pricing.rates import DiscountTier, RateSnapshot

_HUNDRED = Decimal(100)
_PERCENT_QUANTUM = Decimal("0.01")


def effective_percent(
    quantity: int,
    rates: RateSnapshot,
    base_for: Callable[[int], Decimal],
    *,
    rush_fraction: Decimal,
    customer_fraction: Decimal,
) -> tuple[Decimal, DiscountTier | None]:
    """Return the discount percent actually applied, and the tier that triggered it.

    ``base_for`` is a callable mapping a quantity to that order's pre-adjustment
    cost. It is passed in rather than imported so this module stays free of the
    line-building code that calls it.

    The total for a quantity is ``base(q) * (1 + rush - discount - customer)``
    times a constant margin factor, so requiring the bracketed term to be
    non-decreasing is exactly the monotonicity condition.
    """
    tier = rates.discounts.tier_for(quantity)
    if tier is None:
        return Decimal(0), None

    if not rates.guard_tier_cliffs:
        return tier.percent, tier

    applied = Decimal(0)
    for candidate in rates.discounts.tiers:
        if candidate.min_quantity > quantity:
            break

        wanted = candidate.percent / _HUNDRED
        # A tier starting at 1 applies to every order, so it has no step to guard.
        if candidate.min_quantity <= 1:
            applied = wanted
            continue

        base_at = base_for(candidate.min_quantity)
        base_below = base_for(candidate.min_quantity - 1)
        if base_at <= 0:
            applied = wanted
            continue

        value_below = base_below * (1 + rush_fraction - applied - customer_fraction)
        ceiling = 1 + rush_fraction - customer_fraction - (value_below / base_at)
        applied = min(wanted, max(Decimal(0), ceiling))

    # `applied` is the capped fraction for the tier this order falls into, and is
    # never above that tier's nominal rate. Round *down* to a presentable figure:
    # a smaller discount can only raise the total, so it cannot reintroduce a cliff.
    percent = (applied * _HUNDRED).quantize(_PERCENT_QUANTUM, rounding=ROUND_DOWN)
    return percent, tier
