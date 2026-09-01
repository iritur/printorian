"""That `prepared_cost` divides the plate's totals by the line's quantity.

The plate's `print_minutes` and `filament_grams` are the **whole bed's** — that is
already how `production.plates.attach_plate` reads them when it overwrites the
job's `estimated_minutes` and `grams_required` — while the pricing engine wants
per-unit figures and multiplies by quantity itself. `reprice.prepared_cost` is
where the division happens, once, and this file is what holds it there.

**It used to be covered through the intake sweep**, by a test that queued a line of
three against a three-up plate. That order no longer queues: the fourth review of
[#92](https://github.com/iritur/printorian/pull/92) found that nothing records a
plate's bed *footprint*, so `workers/plate_admission` refuses any plate holding
more than one copy and the sweep can no longer reach the arithmetic. The
arithmetic is still right and still load-bearing — the refusal is about the bed's
size, not about the money — so the coverage moved down here, where it belongs
anyway: this is pricing, and pricing needs no database.

Pure, and the expected figure is written out of `pricing.price` twice rather than
by calling `prepared_cost`, for the reason `_intake_cache_support` gives: a test
that calls the thing it is testing to work out the expected answer asserts only
that the function is deterministic.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    prepared_cost,
    price,
)
from printorian.core.units import Duration, Mass

RATES = RateSnapshot()

#: Per unit, as the mesh heuristic guessed at quote time.
QUOTED_MINUTES = Decimal(120)
QUOTED_GRAMS = Decimal(50)

#: One unit's worth as the slicer found it. Longer and heavier than the guess, so
#: the difference is an overrun and a sign error would be visible.
PER_UNIT_MINUTES = Decimal(150)
PER_UNIT_GRAMS = Decimal("62.5")

LINE_TOTAL = Decimal(3000)


def a_spec(quantity: int) -> PriceSpec:
    return PriceSpec(
        estimate=PrintEstimate(
            print_time=Duration(QUOTED_MINUTES), material_mass=Mass(QUOTED_GRAMS)
        ),
        material=MaterialPrice(spec_code="pla-white", price_per_gram=Decimal("2.40")),
        quantity=quantity,
        colors=("white",),
        include_shipping=False,
    )


def expected(quantity: int) -> Decimal:
    """The anchor plus the difference, computed here out of the engine itself."""
    quoted = a_spec(quantity)
    prepared = quoted.with_changes(
        estimate=PrintEstimate(
            print_time=Duration(PER_UNIT_MINUTES), material_mass=Mass(PER_UNIT_GRAMS)
        )
    )
    return LINE_TOTAL + price(prepared, RATES).total.amount - price(quoted, RATES).total.amount


def test_a_three_up_plate_is_priced_as_three_units_of_work() -> None:
    """The bed's totals, divided by three, are one unit's work.

    Drop the `/ units` in `reprice.prepared_cost` and this reprices a line of three
    at three times the per-unit truth: an overrun of roughly two whole units'
    money, recorded on ADR-0013's table as measured.
    """
    cost = prepared_cost(
        quoted=a_spec(3),
        quoted_cost=LINE_TOTAL,
        rates=RATES,
        print_minutes=PER_UNIT_MINUTES * 3,
        total_grams=PER_UNIT_GRAMS * 3,
    )

    assert cost == expected(3)


def test_a_one_up_plate_priced_against_a_line_of_three_underprices_it() -> None:
    """Why the caller has to have earned the division, stated as a number.

    Hand this the totals of a **one-up** plate while the line says three, and it
    divides them by three: a third of the truth per unit, priced *under* the quote
    when the plate is in fact an overrun. That lands comfortably inside ADR-0013's
    band, the order queues, the machine prints a third of what was sold, and the
    variance records the estimate as accurate.

    Nothing in `prepared_cost` can detect it — the plate's layout is not an input —
    which is why `workers/plate_admission` refuses a plate whose recorded `copies`
    is not the line's quantity, and refuses a plate that does not say.
    """
    honest = prepared_cost(
        quoted=a_spec(3),
        quoted_cost=LINE_TOTAL,
        rates=RATES,
        print_minutes=PER_UNIT_MINUTES * 3,
        total_grams=PER_UNIT_GRAMS * 3,
    )
    from_a_one_up_plate = prepared_cost(
        quoted=a_spec(3),
        quoted_cost=LINE_TOTAL,
        rates=RATES,
        print_minutes=PER_UNIT_MINUTES,
        total_grams=PER_UNIT_GRAMS,
    )

    assert honest > LINE_TOTAL
    assert from_a_one_up_plate < LINE_TOTAL
    assert from_a_one_up_plate < honest
