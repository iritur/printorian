"""The arithmetic behind choosing a box.

Pure functions, so no database and no clock — and that is the point of keeping
them apart from the bench's own tests. Every claim here is one somebody will
eventually want to argue with, and each is stated in the form the argument takes:
what happens when the parcel is turned over, what happens to ten flat parts, and
what the carrier will actually bill.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.packaging import (
    batch_box,
    chargeable_grams,
    fits,
    needs_wrap,
    stack_box,
    volumetric_grams,
)
from tests.unit._packaging_support import mm

# ------------------------------------------------------------------ geometry


def test_a_parcel_may_be_turned_over_to_fit() -> None:
    """Comparing axis to axis would refuse a box that obviously fits."""
    assert fits(mm(200, 150, 80), mm(70, 190, 140))


def test_a_batch_is_stacked_along_its_thinnest_axis() -> None:
    """Ten flat brackets become a stack, not ten separate footprints."""
    assert stack_box(mm(190, 140, 7), 10) == mm(190, 140, 70)


def test_the_batch_estimate_over_states_rather_than_under_states() -> None:
    """The safe direction, deliberately.

    Two stacks are assumed to sit on each other rather than side by side, which
    is wrong for anything that would fit alongside — and wrong the way that gets
    a box one size too large rather than one the parcel does not go into.
    """
    together = batch_box([mm(190, 140, 20), mm(100, 80, 30)])
    assert together == mm(190, 140, 50)


def test_an_empty_batch_has_no_size() -> None:
    assert batch_box([]) == mm(0, 0, 0)


def test_volumetric_weight_is_the_carrier_s_arithmetic() -> None:
    """190 × 140 × 70 mm over a 5000 divisor is 372.4 g, and nothing else."""
    assert volumetric_grams(mm(190, 140, 70)) == Decimal("372.4")


def test_a_parcel_is_billed_on_whichever_is_greater() -> None:
    light_and_large = chargeable_grams(Decimal(200), mm(300, 220, 120))
    assert light_and_large == volumetric_grams(mm(300, 220, 120))
    heavy_and_small = chargeable_grams(Decimal(4000), mm(190, 140, 70))
    assert heavy_and_small == Decimal(4000)


def test_unmeasured_geometry_is_not_a_licence_to_skip_the_film() -> None:
    """`None` means nobody measured, which is not the same as "thick enough"."""
    assert needs_wrap(None) is False
    assert needs_wrap(Decimal("0.6")) is True
    assert needs_wrap(Decimal("1.2")) is False
