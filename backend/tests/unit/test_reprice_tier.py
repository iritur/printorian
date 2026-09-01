"""The one direction in which `prepared_cost`'s difference is not conservative.

`pricing.reprice.prepared_cost` deliberately prices a *difference* rather than a
fresh total, because the customer tier and the per-line quote cannot be recovered
from the order (ADR-0013's amendment). Cancelling the tier is safe in almost every
direction: a delta priced at the snapshot's margin instead of at a discount tier's
lower one is *larger* than the truth, and ADR-0013's band is one-sided, so the
error holds a job for a person.

**Except when the tier's `margin_percent_override` is above the snapshot margin.**
A negotiated book that prices at 45% where the farm's default is 30% marks the
change up harder than the untiered difference does, so the difference *understates*
the overrun — and an overrun that should have gone to `PRICE_REVIEW` can land
inside the band and queue itself.

That is not a bug in `prepared_cost`; it is the cost of the design, and this file
is what stops it being rediscovered as a surprise. It measures the gap rather than
asserting it away, and asserts its **sign**, because the sign is the part that
decides whether the failure is safe.

Pure pricing, no database: the tier does not exist on the order at all, so there is
nothing for the intake path to pass and nothing there to assert against.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing import (
    CustomerTier,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    prepared_cost,
    price,
)
from printorian.core.units import Duration, Mass

RATES = RateSnapshot()

#: What the mesh heuristic guessed, per unit.
QUOTED_MINUTES = Decimal(120)
QUOTED_GRAMS = Decimal(50)

#: What the slicer found. Longer and heavier, so the delta is an overrun and the
#: band is the thing being talked about.
PLATE_MINUTES = Decimal(150)
PLATE_GRAMS = Decimal("62.5")

#: A negotiated book that prices *above* the farm's default 30%. This is the whole
#: subject of the file: below it, the difference overstates and is safe.
RICH_TIER = CustomerTier(code="bespoke", margin_percent_override=Decimal(45))


def a_spec(quantity: int = 1) -> PriceSpec:
    return PriceSpec(
        estimate=PrintEstimate(
            print_time=Duration(QUOTED_MINUTES), material_mass=Mass(QUOTED_GRAMS)
        ),
        material=MaterialPrice(spec_code="pla-white", price_per_gram=Decimal("2.40")),
        quantity=quantity,
        colors=("white",),
        include_shipping=False,
    )


def _with_the_plate(spec: PriceSpec) -> PriceSpec:
    """The same question, asked with the slicer's numbers instead of the mesh's."""
    units = Decimal(spec.quantity)
    return spec.with_changes(
        estimate=PrintEstimate(
            print_time=Duration(PLATE_MINUTES / units),
            material_mass=Mass(PLATE_GRAMS / units),
        )
    )


def test_a_tier_priced_above_the_snapshot_margin_understates_the_overrun() -> None:
    """The delta the difference records is smaller than the delta the customer sees.

    `prepared_cost` takes no tier, so it prices the change at
    `rates.margin_percent`. A customer whose book says 45% is charged more for the
    same change, so the *true* prepared cost is higher than the one recorded — and
    a variance is judged on the recorded one.
    """
    quoted = a_spec()
    quoted_cost = price(quoted, RATES, RICH_TIER).total.amount

    recorded = prepared_cost(
        quoted=quoted,
        quoted_cost=quoted_cost,
        rates=RATES,
        print_minutes=PLATE_MINUTES,
        total_grams=PLATE_GRAMS,
    )
    truth = price(_with_the_plate(quoted), RATES, RICH_TIER).total.amount

    # Both are overruns; the recorded one is the smaller of the two, which is the
    # direction that dispatches rather than the direction that holds.
    assert recorded > quoted_cost
    assert recorded < truth


def test_a_tier_priced_below_the_snapshot_margin_overstates_it_instead() -> None:
    """The safe direction, asserted so the two are not confused for each other.

    Every tier that discounts — which is what a loyalty ladder does — makes the
    difference *larger* than the truth. ADR-0013's band is one-sided, so a delta
    that is too large can only hold a job for a person to look at.
    """
    generous = CustomerTier(code="wholesale", margin_percent_override=Decimal(15))
    quoted = a_spec()
    quoted_cost = price(quoted, RATES, generous).total.amount

    recorded = prepared_cost(
        quoted=quoted,
        quoted_cost=quoted_cost,
        rates=RATES,
        print_minutes=PLATE_MINUTES,
        total_grams=PLATE_GRAMS,
    )
    truth = price(_with_the_plate(quoted), RATES, generous).total.amount

    assert recorded > truth


def test_the_understatement_can_carry_an_overrun_inside_the_band() -> None:
    """What the sign of that error costs, stated as the thing ADR-0013 decides.

    Not a contrived margin: 45% against the farm's 30%, on a plate that came in a
    quarter longer and a quarter heavier than the mesh guessed — the ordinary size
    of a mesh-heuristic miss. The overrun the customer's own book produces is
    15.48%, over the band; the one `prepared_cost` records is 13.88%, under it. So
    the order queues itself instead of going to `PRICE_REVIEW`.

    If this test ever fails because both figures fall on the same side of the
    band, the gap has been closed or the rates have moved — read the two ratios it
    prints before deciding which.
    """
    tolerance = Decimal("0.15")
    quoted = a_spec()
    quoted_cost = price(quoted, RATES, RICH_TIER).total.amount

    recorded = prepared_cost(
        quoted=quoted,
        quoted_cost=quoted_cost,
        rates=RATES,
        print_minutes=PLATE_MINUTES,
        total_grams=PLATE_GRAMS,
    )
    truth = price(_with_the_plate(quoted), RATES, RICH_TIER).total.amount

    recorded_ratio = (recorded - quoted_cost) / quoted_cost
    true_ratio = (truth - quoted_cost) / quoted_cost

    assert true_ratio > tolerance, f"true overrun {true_ratio} is not over the band"
    assert recorded_ratio <= tolerance, f"recorded overrun {recorded_ratio} is over the band"
