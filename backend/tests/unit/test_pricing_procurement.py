"""Buying in a filament the farm does not stock.

The load-bearing decision: the charge is per *order*, not per unit. One delivery
covers the whole plate however many copies it makes, so multiplying it by
quantity would charge a customer more for the same single purchase.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing import (
    MATERIAL_PROCUREMENT,
    Breakdown,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
)
from printorian.core.units import Duration, Mass


def make_spec(**changes: object) -> PriceSpec:
    base: dict[str, object] = {
        "estimate": PrintEstimate(
            print_time=Duration(Decimal(120)), material_mass=Mass(Decimal(50))
        ),
        "material": MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
        "quantity": 1,
    }
    return PriceSpec(**{**base, **changes})  # type: ignore[arg-type]


def _procured(**changes: object) -> PriceSpec:
    """A spec whose filament the farm does not hold."""
    return make_spec(
        material=MaterialPrice(
            spec_code="pla-black", price_per_gram=Decimal("2.40"), needs_procurement=True
        ),
        **changes,
    )


def _procurement(breakdown: Breakdown) -> Decimal:
    return next(line.amount.amount for line in breakdown.lines if line.code == MATERIAL_PROCUREMENT)


def test_a_stocked_filament_costs_nothing_extra() -> None:
    """No line at all rather than a zero one, so a customer is not left
    wondering what a 0 ₽ charge is for."""
    breakdown = price(make_spec(), RateSnapshot())

    assert not any(line.code == MATERIAL_PROCUREMENT for line in breakdown.lines)


def test_a_filament_that_must_be_bought_is_charged_for() -> None:
    rates = RateSnapshot()
    breakdown = price(_procured(), rates)

    line = next(line for line in breakdown.lines if line.code == MATERIAL_PROCUREMENT)
    assert line.amount.amount == rates.material_procurement_flat


def test_procurement_is_charged_once_however_many_are_ordered() -> None:
    """One delivery covers the whole plate. Multiplying it by quantity would
    charge a customer more for the same single purchase."""
    rates = RateSnapshot()

    one = price(_procured(quantity=1), rates)
    fifty = price(_procured(quantity=50), rates)

    assert _procurement(one) == _procurement(fifty) == rates.material_procurement_flat


def test_procurement_still_leaves_the_lines_summing_to_the_total() -> None:
    """The invariant every other line obeys — a new one must not break it."""
    breakdown = price(_procured(quantity=3), RateSnapshot())

    assert sum((line.amount.amount for line in breakdown.lines), Decimal(0)) == (
        breakdown.total.amount
    )


def test_the_charge_is_configuration_not_a_constant() -> None:
    cheap = RateSnapshot(material_procurement_flat=Decimal(100))
    dear = RateSnapshot(material_procurement_flat=Decimal(900))

    assert _procurement(price(_procured(), cheap)) == Decimal(100)
    assert _procurement(price(_procured(), dear)) == Decimal(900)


def test_the_rate_is_part_of_the_pinned_snapshot() -> None:
    """A quote must not change because the farm's courier got dearer later."""
    assert (
        RateSnapshot().snapshot_id
        != RateSnapshot(material_procurement_flat=Decimal(999)).snapshot_id
    )


def test_procurement_follows_the_whole_plate_not_the_priced_product() -> None:
    """The bug this guards against.

    A plate names one product per colour, but the engine prices from one. Reading
    stock off *that* product alone meant a four-colour plate with three colours
    off the shelf was quoted with no procurement charge whenever the dearest
    happened to be the one in stock. The router now sets the flag when any chosen
    product is missing — this asserts the engine honours it regardless of which
    product carries the price.
    """
    dearest_in_stock_but_another_is_not = MaterialPrice(
        spec_code="pla-blue", price_per_gram=Decimal("2.40"), needs_procurement=True
    )
    breakdown = price(make_spec(material=dearest_in_stock_but_another_is_not), RateSnapshot())

    assert _procurement(breakdown) == RateSnapshot().material_procurement_flat
