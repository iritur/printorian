"""The pricing engine's invariants.

Every V1 pricing defect that these would have caught is named in the test that
catches it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from printorian.contexts.pricing import (
    ADJUSTMENT_RUSH,
    ADJUSTMENT_VOLUME_DISCOUNT,
    ENGINE_VERSION,
    LABOR_ENGINEERING,
    LABOR_SETUP,
    LOGISTICS_PACKAGING,
    LOGISTICS_SHIPPING,
    MARGIN,
    MATERIAL,
    MATERIAL_PURGE,
    RISK_FAILURE_BUFFER,
    Category,
    CustomerTier,
    DiscountLadder,
    DiscountTier,
    FinishOption,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
)
from printorian.core.errors import ValidationError
from printorian.core.money import Currency, Money, sum_money
from printorian.core.units import Duration, Mass


def make_spec(**changes: object) -> PriceSpec:
    base = PriceSpec(
        estimate=PrintEstimate(print_time=Duration.from_hours(4), material_mass=Mass(120)),
        material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
    )
    return base.with_changes(**changes) if changes else base


RATES = RateSnapshot()
LADDER_RATES = RateSnapshot(
    discounts=DiscountLadder(tiers=(DiscountTier(10, Decimal(5)), DiscountTier(50, Decimal(12))))
)


# ------------------------------------------------------------- core identity


def test_lines_always_sum_to_the_total() -> None:
    """The property a customer actually checks: the rows add up."""
    breakdown = price(make_spec(quantity=7, rush=True), LADDER_RATES)
    assert sum_money([line.amount for line in breakdown.lines], Currency.RUB) == breakdown.total


def test_cost_plus_margin_equals_total() -> None:
    breakdown = price(make_spec(), RATES)
    assert (breakdown.cost + breakdown.margin).rounded() == breakdown.total


def test_categories_partition_the_total() -> None:
    breakdown = price(make_spec(quantity=3, finishes=(FinishOption(code="polish"),)), RATES)
    assert sum_money(list(breakdown.by_category().values()), Currency.RUB) == breakdown.total


def test_result_is_deterministic() -> None:
    spec = make_spec(quantity=5, rush=True)
    assert price(spec, RATES).total == price(spec, RATES).total


def test_breakdown_records_how_it_was_produced() -> None:
    """Without these two fields a historical quote cannot be reproduced."""
    breakdown = price(make_spec(), RATES)
    assert breakdown.engine_version == ENGINE_VERSION
    assert breakdown.rate_snapshot_id == RATES.snapshot_id
    assert breakdown.rate_snapshot_id.startswith("rates_")


def test_identical_rates_hash_identically_and_different_rates_do_not() -> None:
    assert RateSnapshot().snapshot_id == RateSnapshot().snapshot_id
    assert RateSnapshot().snapshot_id != RateSnapshot(margin_percent=Decimal(31)).snapshot_id
    assert RateSnapshot().snapshot_id != LADDER_RATES.snapshot_id


# --------------------------------------------------- quantity semantics (V1 bug)


def test_material_and_machine_scale_with_quantity() -> None:
    one = price(make_spec(quantity=1), RATES)
    ten = price(make_spec(quantity=10), RATES)
    assert ten.amount_of(MATERIAL) == one.amount_of(MATERIAL) * 10


def test_setup_and_shipping_are_charged_once_per_job() -> None:
    """V1's web calculator multiplied these by quantity and its desktop one did not."""
    one = price(make_spec(quantity=1), RATES)
    ten = price(make_spec(quantity=10), RATES)
    assert ten.amount_of(LABOR_SETUP) == one.amount_of(LABOR_SETUP)
    assert ten.amount_of(LOGISTICS_SHIPPING) == one.amount_of(LOGISTICS_SHIPPING)


def test_packaging_is_charged_per_unit() -> None:
    one = price(make_spec(quantity=1), RATES)
    ten = price(make_spec(quantity=10), RATES)
    assert ten.amount_of(LOGISTICS_PACKAGING) == one.amount_of(LOGISTICS_PACKAGING) * 10


def test_engineering_for_a_resize_is_charged_once_however_many_copies() -> None:
    one = price(make_spec(scale=Decimal("1.5"), quantity=1), RATES)
    ten = price(make_spec(scale=Decimal("1.5"), quantity=10), RATES)
    assert one.amount_of(LABOR_ENGINEERING) == ten.amount_of(LABOR_ENGINEERING)
    assert not one.amount_of(LABOR_ENGINEERING).is_zero


def test_no_engineering_line_at_original_scale() -> None:
    assert price(make_spec(), RATES).line(LABOR_ENGINEERING) is None


@pytest.mark.parametrize("quantity", [1, 2, 5, 9, 10, 11, 49, 50, 51, 200])
def test_total_never_decreases_as_quantity_grows(quantity: int) -> None:
    lower = price(make_spec(quantity=quantity), LADDER_RATES).total
    higher = price(make_spec(quantity=quantity + 1), LADDER_RATES).total
    assert higher >= lower


@pytest.mark.parametrize("quantity", [1, 2, 9, 10, 11, 49, 50, 51, 200])
def test_unit_price_never_increases_as_quantity_grows(quantity: int) -> None:
    """Ordering one more must never make each item dearer, at any tier boundary."""
    lower = price(make_spec(quantity=quantity), LADDER_RATES).unit_price
    higher = price(make_spec(quantity=quantity + 1), LADDER_RATES).unit_price
    assert higher <= lower


# ------------------------------------------------------------------ options


def test_multicolor_charges_purge_waste() -> None:
    """Extra colours cost real filament; hiding that would eat the margin."""
    single = price(make_spec(), RATES)
    quad = price(make_spec(colors=("black", "red", "white", "blue")), RATES)

    assert single.line(MATERIAL_PURGE) is None
    assert not quad.amount_of(MATERIAL_PURGE).is_zero
    assert quad.total > single.total


def test_repeating_one_colour_costs_no_purge() -> None:
    """Purge is spent flushing the nozzle *between* filaments.

    A customer who asks for two slots and picks white for both has made a
    single-colour plate. Charging per slot would bill them for a flush the
    machine never performs — and the configurator lets them do exactly this.
    """
    single = price(make_spec(), RATES)
    repeated = price(make_spec(colors=("white", "white", "white")), RATES)

    assert repeated.line(MATERIAL_PURGE) is None
    assert repeated.total == single.total


def test_colour_names_differing_only_in_case_are_one_filament() -> None:
    """Two screens disagreeing about capitalisation must not invent a purge."""
    assert price(make_spec(colors=("White", "white")), RATES).line(MATERIAL_PURGE) is None


def test_more_than_four_colors_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_spec(colors=("a", "b", "c", "d", "e"))
    assert excinfo.value.code == "error.pricing.too_many_colors"


def test_finishes_add_one_line_each_and_scale_per_unit() -> None:
    finishes = (
        FinishOption(code="polish", labor_hours=Decimal("0.5")),
        FinishOption(code="paint", labor_hours=Decimal(1), flat_fee=Decimal(200)),
    )
    breakdown = price(make_spec(quantity=3, finishes=finishes), RATES)

    polish = breakdown.amount_of("postprocess.polish")
    assert polish == Money(Decimal("0.5") * 500 * 3)
    assert breakdown.amount_of("postprocess.paint") == Money((Decimal(1) * 500 + 200) * 3)


def test_duplicate_finishes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_spec(finishes=(FinishOption(code="polish"), FinishOption(code="polish")))


def test_collection_removes_the_shipping_line_entirely() -> None:
    """A zero line invites "what is this?"; no line is the honest answer."""
    assert price(make_spec(include_shipping=False), RATES).line(LOGISTICS_SHIPPING) is None


def test_rush_adds_a_surcharge_line_and_raises_the_total() -> None:
    calm = price(make_spec(), RATES)
    rushed = price(make_spec(rush=True), RATES)

    assert calm.line(ADJUSTMENT_RUSH) is None
    assert rushed.total > calm.total
    assert not rushed.amount_of(ADJUSTMENT_RUSH).is_negative


# ---------------------------------------------------------------- discounts


def test_volume_discount_appears_as_a_negative_line() -> None:
    breakdown = price(make_spec(quantity=10), LADDER_RATES)
    discount = breakdown.line(ADJUSTMENT_VOLUME_DISCOUNT)
    assert discount is not None
    assert discount.is_credit
    assert discount.basis.tier_min_quantity == 10


def test_no_discount_line_below_the_first_tier() -> None:
    assert price(make_spec(quantity=9), LADDER_RATES).line(ADJUSTMENT_VOLUME_DISCOUNT) is None


def test_highest_applicable_tier_wins() -> None:
    breakdown = price(make_spec(quantity=60), LADDER_RATES)
    line = breakdown.line(ADJUSTMENT_VOLUME_DISCOUNT)

    assert breakdown.amount_of(ADJUSTMENT_VOLUME_DISCOUNT).is_negative
    assert line is not None
    assert line.basis.tier_min_quantity == 50


def test_tier_cliff_guard_caps_the_discount_below_its_nominal_rate() -> None:
    """A raw 12% at qty 50 would make 50 units cost less than 49.

    The guard reduces the applied percent to the largest value that keeps the
    total non-decreasing, and reports the percent it actually used.
    """
    line = price(make_spec(quantity=50), LADDER_RATES).line(ADJUSTMENT_VOLUME_DISCOUNT)
    assert line is not None
    assert Decimal(0) < line.basis.percent < Decimal(12)


def test_disabling_the_guard_restores_raw_step_tiers_and_their_cliff() -> None:
    """Documents what the guard is protecting against, so the trade-off is visible."""
    raw = RateSnapshot(discounts=LADDER_RATES.discounts, guard_tier_cliffs=False)

    at_49 = price(make_spec(quantity=49), raw).total
    at_50 = price(make_spec(quantity=50), raw).total
    assert at_50 < at_49  # the cliff: more work, less money

    guarded_50 = price(make_spec(quantity=50), LADDER_RATES).total
    assert guarded_50 >= price(make_spec(quantity=49), LADDER_RATES).total


def test_guard_reports_a_presentable_percent() -> None:
    line = price(make_spec(quantity=50), LADDER_RATES).line(ADJUSTMENT_VOLUME_DISCOUNT)
    assert line is not None
    assert line.basis.percent == line.basis.percent.quantize(Decimal("0.01"))


def test_guard_leaves_a_tier_alone_when_it_causes_no_cliff() -> None:
    """A modest tier is applied at its full nominal rate."""
    gentle = RateSnapshot(discounts=DiscountLadder(tiers=(DiscountTier(10, Decimal(2)),)))
    line = price(make_spec(quantity=20), gentle).line(ADJUSTMENT_VOLUME_DISCOUNT)
    assert line is not None
    assert line.basis.percent == Decimal("2.00")


def test_an_inverting_ladder_is_rejected_at_construction() -> None:
    """A bigger order must never be discounted less than a smaller one."""
    with pytest.raises(ValidationError) as excinfo:
        DiscountLadder(tiers=(DiscountTier(10, Decimal(20)), DiscountTier(50, Decimal(5))))
    assert excinfo.value.code == "error.pricing.ladder_inverts"


def test_duplicate_tier_quantities_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DiscountLadder(tiers=(DiscountTier(10, Decimal(5)), DiscountTier(10, Decimal(7))))


def test_ladder_is_normalized_to_ascending_order() -> None:
    ladder = DiscountLadder(tiers=(DiscountTier(50, Decimal(12)), DiscountTier(10, Decimal(5))))
    assert [tier.min_quantity for tier in ladder.tiers] == [10, 50]


def test_customer_tier_discount_and_margin_override() -> None:
    standard = price(make_spec(), RATES)
    negotiated = price(
        make_spec(),
        RATES,
        CustomerTier(code="wholesale", discount_percent=Decimal(10)),
    )
    assert negotiated.total < standard.total

    thin = price(make_spec(), RATES, CustomerTier(code="key", margin_percent_override=Decimal(5)))
    assert thin.amount_of(MARGIN) < standard.amount_of(MARGIN)


# ------------------------------------------------------------- rule ordering


def test_margin_is_taken_after_adjustments_not_before() -> None:
    """Otherwise a discount would silently come out of margin rather than price."""
    breakdown = price(make_spec(quantity=10, rush=True), LADDER_RATES)
    pre_margin = sum_money(
        [line.amount for line in breakdown.lines if line.category is not Category.MARGIN],
        Currency.RUB,
    )
    expected = (pre_margin * (RATES.margin_percent / Decimal(100))).rounded()
    assert breakdown.amount_of(MARGIN) == expected


def test_failure_buffer_excludes_logistics() -> None:
    """Reprinting a failed part does not mean re-posting the parcel."""
    breakdown = price(make_spec(), RATES)
    line = breakdown.line(RISK_FAILURE_BUFFER)
    assert line is not None
    assert LOGISTICS_SHIPPING not in line.basis.of_codes
    assert LOGISTICS_PACKAGING not in line.basis.of_codes
    assert MATERIAL in line.basis.of_codes


def test_every_line_explains_itself() -> None:
    breakdown = price(make_spec(quantity=4, rush=True, scale=Decimal(2)), LADDER_RATES)
    for line in breakdown.lines:
        assert line.basis.kind is not None
        # ADR-0012: the basis is structured data the client renders, never prose.
        assert not isinstance(line.basis.rate, str)


# ------------------------------------------------------------------ guards


def test_engine_uses_no_floats_anywhere() -> None:
    breakdown = price(make_spec(quantity=3, rush=True), LADDER_RATES)
    for line in breakdown.lines:
        assert isinstance(line.amount.amount, Decimal)
        for value in (line.basis.quantity, line.basis.rate, line.basis.percent):
            assert value is None or isinstance(value, Decimal)


def test_zero_and_negative_inputs_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PrintEstimate(print_time=Duration(0), material_mass=Mass(10))
    with pytest.raises(ValidationError):
        MaterialPrice(spec_code="x", price_per_gram=Decimal(-1))
    with pytest.raises(ValidationError):
        make_spec(quantity=0)
    with pytest.raises(ValidationError):
        make_spec(scale=Decimal(0))


def test_with_changes_rejects_unknown_fields() -> None:
    """A typo must not silently return an unchanged spec and a wrong price."""
    with pytest.raises(ValidationError) as excinfo:
        make_spec(quantiy=5)  # deliberate typo
    assert excinfo.value.code == "error.pricing.unknown_field"


def test_negative_rates_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RateSnapshot(labor_rate_per_hour=Decimal(-1))
