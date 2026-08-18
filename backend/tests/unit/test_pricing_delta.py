"""Option deltas — the scenario's "+120 in labor, −260 in material" preview."""

from __future__ import annotations

from decimal import Decimal

import pytest

from printorian.contexts.pricing import (
    ADJUSTMENT_RUSH,
    ADJUSTMENT_VOLUME_DISCOUNT,
    MATERIAL,
    Category,
    DiscountLadder,
    DiscountTier,
    FinishOption,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    diff,
    preview,
    price,
)
from printorian.core.errors import ValidationError
from printorian.core.money import Currency, Money
from printorian.core.units import Duration, Mass

RATES = RateSnapshot(
    discounts=DiscountLadder(tiers=(DiscountTier(10, Decimal(5)), DiscountTier(50, Decimal(12))))
)


def make_spec(**changes: object) -> PriceSpec:
    base = PriceSpec(
        estimate=PrintEstimate(print_time=Duration.from_hours(4), material_mass=Mass(120)),
        material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
    )
    return base.with_changes(**changes) if changes else base


# ----------------------------------------------------- monotonicity sweep


@pytest.mark.parametrize("quantity", range(1, 101))
def test_price_curve_never_decreases_across_the_whole_range(quantity: int) -> None:
    """Exhaustive: no quantity anywhere in 1..100 is cheaper than the one below it."""
    if quantity == 1:
        return
    assert price(make_spec(quantity=quantity), RATES).total >= (
        price(make_spec(quantity=quantity - 1), RATES).total
    )


# ------------------------------------------------------------------ diff


def test_diff_of_identical_breakdowns_is_all_zero() -> None:
    breakdown = price(make_spec(), RATES)
    delta = diff(breakdown, breakdown)

    assert delta.changed == ()
    assert delta.total_change.is_zero


def test_line_changes_sum_to_the_total_change() -> None:
    """The preview must add up, or the customer is told a different story twice."""
    delta = preview(make_spec(), RATES, quantity=10)
    total = Money.zero(Currency.RUB)
    for line in delta.lines:
        total = total + line.change
    assert total.rounded() == delta.total_change


def test_adding_a_finish_shows_it_as_a_new_line() -> None:
    delta = preview(
        make_spec(), RATES, finishes=(FinishOption(code="polish", labor_hours=Decimal("0.5")),)
    )
    new_lines = [line for line in delta.changed if line.is_new]

    assert any(line.code == "postprocess.polish" for line in new_lines)
    assert not delta.total_change.is_negative


def test_removing_an_option_shows_it_as_a_removed_line() -> None:
    with_finish = make_spec(finishes=(FinishOption(code="polish", labor_hours=Decimal(1)),))
    delta = diff(price(with_finish, RATES), price(make_spec(), RATES))

    removed = [line for line in delta.changed if line.is_removed]
    assert any(line.code == "postprocess.polish" for line in removed)
    assert delta.total_change.is_negative


def test_a_cheaper_material_shows_a_decrease_in_the_material_line() -> None:
    """The scenario's example: swapping an option lowers one line, raises another."""
    delta = preview(
        make_spec(),
        RATES,
        material=MaterialPrice(spec_code="pla-basic", price_per_gram=Decimal("1.20")),
    )

    material = next(line for line in delta.changed if line.code == MATERIAL)
    assert material.change.is_negative
    assert delta.total_change.is_negative
    assert material in delta.decreases


def test_rush_appears_as_an_increase() -> None:
    delta = preview(make_spec(), RATES, rush=True)

    assert any(line.code == ADJUSTMENT_RUSH for line in delta.increases)
    assert delta.total_change > Money.zero(Currency.RUB)


def test_quantity_increase_triggers_the_discount_line() -> None:
    delta = preview(make_spec(quantity=5), RATES, quantity=20)
    discount = next(line for line in delta.changed if line.code == ADJUSTMENT_VOLUME_DISCOUNT)

    assert discount.is_new
    assert discount.change.is_negative


def test_increases_and_decreases_partition_the_changed_lines() -> None:
    delta = preview(make_spec(quantity=5), RATES, quantity=60, rush=True)

    assert set(delta.increases) | set(delta.decreases) == set(delta.changed)
    assert not set(delta.increases) & set(delta.decreases)


def test_delta_carries_categories_for_grouping() -> None:
    delta = preview(
        make_spec(), RATES, finishes=(FinishOption(code="paint", flat_fee=Decimal(300)),)
    )
    polish = next(line for line in delta.changed if line.code == "postprocess.paint")
    assert polish.category is Category.LABOR


def test_delta_flags_when_the_two_sides_are_not_comparable() -> None:
    """Comparing across rate snapshots mixes a rule change into the customer's choice."""
    other_rates = RateSnapshot(margin_percent=Decimal(40))
    delta = diff(price(make_spec(), RATES), price(make_spec(), other_rates))

    assert not delta.comparable


def test_same_rates_are_comparable() -> None:
    assert preview(make_spec(), RATES, quantity=3).comparable


def test_mismatched_currencies_are_rejected() -> None:
    euro_rates = RateSnapshot(currency=Currency.EUR)
    with pytest.raises(ValidationError) as excinfo:
        diff(price(make_spec(), RATES), price(make_spec(), euro_rates))
    assert excinfo.value.code == "error.pricing.delta_currency_mismatch"


def test_preview_rejects_an_unknown_option_name() -> None:
    with pytest.raises(ValidationError):
        preview(make_spec(), RATES, quantitee=10)


def test_preview_matches_pricing_twice_by_hand() -> None:
    """There is one code path. This pins that claim."""
    spec = make_spec()
    changed = spec.with_changes(quantity=25, rush=True)

    assert (
        preview(spec, RATES, quantity=25, rush=True).total_change
        == (price(changed, RATES).total - price(spec, RATES).total).rounded()
    )
