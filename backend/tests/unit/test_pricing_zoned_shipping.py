"""Shipping priced by zone, and the flat rate it falls back to.

Its own file rather than more of `test_pricing_engine.py`, which is 365 lines
against the 400-line gate.

The first test is the one that matters most: a farm that has drawn no zones must
price exactly as it did before zones existed, which is what makes every pricing
test written before this change a regression guard for it.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing import (
    ADJUSTMENT_RUSH,
    LOGISTICS_PACKAGING,
    LOGISTICS_SHIPPING,
    LOGISTICS_SHIPPING_WEIGHT,
    MARGIN,
    RISK_FAILURE_BUFFER,
    Category,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    ShippingZone,
    ZoneTariffs,
    price,
)
from printorian.core.units import Duration, Mass

#: 120 g a copy, so a kilogram is eight and a third copies — a mass with a
#: recurring decimal in it, deliberately, so a rounding shortcut would show.
MOSCOW = ShippingZone(code="msk", base=Decimal(400), per_kg=Decimal(0), postcode_prefixes=("1",))
CENTRAL = ShippingZone(code="cfo", base=Decimal(550), per_kg=Decimal(60), postcode_prefixes=("3",))
RETIRED = ShippingZone(code="intl", base=Decimal(4000), postcode_prefixes=("9",), enabled=False)

TARIFFS = ZoneTariffs(zones=(MOSCOW, CENTRAL, RETIRED))
FLAT_RATES = RateSnapshot()
ZONED_RATES = RateSnapshot(zones=TARIFFS)


def make_spec(**changes: object) -> PriceSpec:
    base = PriceSpec(
        estimate=PrintEstimate(print_time=Duration.from_hours(4), material_mass=Mass(120)),
        material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
    )
    return base.with_changes(**changes) if changes else base


# ---------------------------------------------------------- the flat fallback


def test_an_empty_zone_table_prices_exactly_as_before_zones_existed() -> None:
    """The regression guard. No farm's existing quote may move because of this."""
    without = price(make_spec(quantity=3, rush=True), RateSnapshot())
    with_empty = price(make_spec(quantity=3, rush=True), RateSnapshot(zones=ZoneTariffs()))
    assert [(line.code, line.amount) for line in without.lines] == [
        (line.code, line.amount) for line in with_empty.lines
    ]
    assert without.total == with_empty.total


def test_a_drawn_table_with_no_destination_still_quotes_the_flat_rate() -> None:
    """The pre-address checkout, which is the contract `RepriceLine` rests on.

    The customer picks "courier" before typing an address; the farm owes them a
    figure then, not after.
    """
    breakdown = price(make_spec(), ZONED_RATES)
    assert breakdown.amount_of(LOGISTICS_SHIPPING).amount == Decimal(400)
    assert breakdown.line(LOGISTICS_SHIPPING_WEIGHT) is None


def test_a_zone_code_nobody_drew_falls_back_rather_than_guessing() -> None:
    """ADR-0007: an unknown destination is not measured, and never Moscow."""
    breakdown = price(make_spec(destination_zone="vladivostok"), ZONED_RATES)
    assert breakdown.amount_of(LOGISTICS_SHIPPING) == FLAT_RATES.money(FLAT_RATES.shipping_flat)


def test_a_retired_zone_falls_back_to_the_flat_rate() -> None:
    """A zone the farm switched off is not a price it is still offering."""
    breakdown = price(make_spec(destination_zone="intl"), ZONED_RATES)
    assert breakdown.amount_of(LOGISTICS_SHIPPING).amount == FLAT_RATES.shipping_flat


def test_collection_has_no_shipping_line_zoned_or_not() -> None:
    breakdown = price(make_spec(include_shipping=False, destination_zone="cfo"), ZONED_RATES)
    assert breakdown.line(LOGISTICS_SHIPPING) is None
    assert breakdown.line(LOGISTICS_SHIPPING_WEIGHT) is None
    assert breakdown.line(LOGISTICS_PACKAGING) is not None


# ------------------------------------------------------------- the zone tariff


def test_a_matched_zone_replaces_the_flat_base() -> None:
    breakdown = price(make_spec(destination_zone="cfo"), ZONED_RATES)
    assert breakdown.amount_of(LOGISTICS_SHIPPING).amount == Decimal(550)


def test_a_per_kilogram_zone_adds_a_second_line_over_the_mass_printed() -> None:
    breakdown = price(make_spec(quantity=10, destination_zone="cfo"), ZONED_RATES)
    weight = breakdown.line(LOGISTICS_SHIPPING_WEIGHT)
    assert weight is not None
    # 10 copies x 120 g = 1.2 kg at 60 ₽/kg.
    assert weight.amount.amount == Decimal(72)
    assert weight.basis.quantity == Decimal("1.2")
    assert weight.basis.unit == "kg"
    assert weight.basis.rate == Decimal(60)


def test_the_two_zone_lines_add_up_to_base_plus_rate_times_mass() -> None:
    breakdown = price(make_spec(quantity=10, destination_zone="cfo"), ZONED_RATES)
    shipping = breakdown.amount_of(LOGISTICS_SHIPPING).amount
    weight = breakdown.amount_of(LOGISTICS_SHIPPING_WEIGHT).amount
    assert shipping + weight == Decimal(550) + Decimal(60) * Decimal("1.2")


def test_a_zone_that_does_not_charge_by_weight_emits_no_weight_line() -> None:
    """Not a zero line — the same convention collection already follows.

    Inside the MKAD the courier charges the same for a keyring and a crate, and a
    «0 ₽» row would only make the customer wonder what it was for.
    """
    breakdown = price(make_spec(quantity=10, destination_zone="msk"), ZONED_RATES)
    assert breakdown.amount_of(LOGISTICS_SHIPPING).amount == Decimal(400)
    assert breakdown.line(LOGISTICS_SHIPPING_WEIGHT) is None


def test_the_zone_base_is_charged_once_however_many_copies() -> None:
    one = price(make_spec(quantity=1, destination_zone="cfo"), ZONED_RATES)
    ten = price(make_spec(quantity=10, destination_zone="cfo"), ZONED_RATES)
    assert one.amount_of(LOGISTICS_SHIPPING) == ten.amount_of(LOGISTICS_SHIPPING)


def test_both_zone_lines_are_logistics() -> None:
    breakdown = price(make_spec(quantity=10, destination_zone="cfo"), ZONED_RATES)
    for code in (LOGISTICS_SHIPPING, LOGISTICS_SHIPPING_WEIGHT):
        line = breakdown.line(code)
        assert line is not None
        assert line.category is Category.LOGISTICS


# ------------------------------------------------------- where they sit in the stack


def test_zoned_shipping_sits_outside_the_failure_buffer() -> None:
    """Reprinting a part does not re-post the parcel — the rule the flat line had."""
    breakdown = price(make_spec(quantity=10, destination_zone="cfo"), ZONED_RATES)
    buffer_line = breakdown.line(RISK_FAILURE_BUFFER)
    assert buffer_line is not None
    assert LOGISTICS_SHIPPING not in buffer_line.basis.of_codes
    assert LOGISTICS_SHIPPING_WEIGHT not in buffer_line.basis.of_codes


def test_zoned_shipping_sits_inside_the_rush_and_margin_base() -> None:
    """Exactly where the flat line sits, asserted because a silent move is invisible."""
    breakdown = price(make_spec(quantity=10, rush=True, destination_zone="cfo"), ZONED_RATES)
    for code in (ADJUSTMENT_RUSH, MARGIN):
        line = breakdown.line(code)
        assert line is not None
        assert LOGISTICS_SHIPPING in line.basis.of_codes
        assert LOGISTICS_SHIPPING_WEIGHT in line.basis.of_codes


def test_a_dearer_zone_raises_the_total_by_more_than_its_own_lines() -> None:
    """Because margin is taken over shipping, as it always was."""
    cheap = price(make_spec(quantity=10, destination_zone="msk"), ZONED_RATES)
    dear = price(make_spec(quantity=10, destination_zone="cfo"), ZONED_RATES)
    zone_difference = (
        dear.amount_of(LOGISTICS_SHIPPING).amount
        + dear.amount_of(LOGISTICS_SHIPPING_WEIGHT).amount
        - cheap.amount_of(LOGISTICS_SHIPPING).amount
    )
    assert dear.total.amount - cheap.total.amount > zone_difference
