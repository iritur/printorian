"""The mesh heuristic.

The estimator is expected to be imprecise — ADR-0013 exists because of that. What
it must not be is *unstable* or *wrong in direction*: a bigger model must estimate
more, denser material must weigh more, and a solid block must never be estimated
as needing more filament than it physically contains.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from printorian.contexts.catalog import EstimationProfile, analyse_stl, estimate
from printorian.core.errors import ValidationError
from tests.unit.test_mesh_analysis import cube_triangles, to_binary_stl


def analyse(size: float = 40.0):
    return analyse_stl(to_binary_stl(cube_triangles(size)))


def test_estimate_is_positive_and_carries_its_profile() -> None:
    prediction = estimate(analyse())

    assert prediction.print_time.minutes > 0
    assert prediction.material_mass.grams > 0
    assert prediction.profile.infill_fraction == Decimal("0.15")


def test_material_never_exceeds_the_solid_volume() -> None:
    """A shell plus partial infill cannot weigh more than a solid block."""
    analysis = analyse(40.0)
    prediction = estimate(analysis)

    solid_grams = analysis.volume.cubic_centimetres * Decimal("1.24")
    assert prediction.material_mass.grams < solid_grams


def test_larger_models_estimate_more() -> None:
    small = estimate(analyse(20.0))
    large = estimate(analyse(60.0))

    assert large.material_mass.grams > small.material_mass.grams
    assert large.print_time.minutes > small.print_time.minutes


def test_scaling_grows_material_faster_than_linearly() -> None:
    """Volume goes with the cube of scale — the thing customers underestimate."""
    base = estimate(analyse(20.0))
    doubled = estimate(analyse(20.0), scale=Decimal(2))

    ratio = doubled.material_mass.grams / base.material_mass.grams
    assert ratio > Decimal(3)


def test_denser_material_weighs_more_for_the_same_geometry() -> None:
    light = estimate(analyse(), EstimationProfile(density_g_per_cm3=Decimal("1.04")))
    heavy = estimate(analyse(), EstimationProfile(density_g_per_cm3=Decimal("1.27")))

    assert heavy.material_mass.grams > light.material_mass.grams
    # Time depends on extruded volume, not on mass, so it must not move.
    assert heavy.print_time.minutes == light.print_time.minutes


def test_more_infill_costs_more_material_and_more_time() -> None:
    sparse = estimate(analyse(), EstimationProfile(infill_fraction=Decimal("0.10")))
    dense = estimate(analyse(), EstimationProfile(infill_fraction=Decimal("0.60")))

    assert dense.material_mass.grams > sparse.material_mass.grams
    assert dense.print_time.minutes > sparse.print_time.minutes


def test_time_includes_a_fixed_setup_component() -> None:
    """Even a tiny model occupies the machine for heat-up and levelling."""
    tiny = estimate(analyse(3.0))
    assert tiny.print_time.minutes > EstimationProfile().setup_minutes


def test_calibration_factor_scales_the_result() -> None:
    """The lever Phase 6 turns once measured jobs disagree with the heuristic."""
    plain = estimate(analyse())
    biased = estimate(analyse(), EstimationProfile(calibration_factor=Decimal("1.20")))

    ratio = biased.material_mass.grams / plain.material_mass.grams
    assert ratio == pytest.approx(Decimal("1.2"), abs=Decimal("0.01"))


def test_shell_and_infill_are_reported_separately() -> None:
    """Keeping the split visible is what makes a surprising estimate diagnosable."""
    prediction = estimate(analyse())

    assert prediction.shell_volume.cubic_centimetres > 0
    assert prediction.infill_volume.cubic_centimetres > 0
    assert prediction.extruded_volume.cubic_centimetres == (
        prediction.shell_volume.cubic_centimetres + prediction.infill_volume.cubic_centimetres
    )


def test_estimate_is_deterministic() -> None:
    analysis = analyse()
    assert estimate(analysis).material_mass.grams == estimate(analysis).material_mass.grams


def test_an_unpriceable_mesh_is_refused() -> None:
    holed = analyse_stl(to_binary_stl(cube_triangles(20.0)[:-2]))

    with pytest.raises(ValidationError) as excinfo:
        estimate(holed)
    assert excinfo.value.code == "error.catalog.mesh_not_priceable"


def test_invalid_profiles_and_scales_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EstimationProfile(infill_fraction=Decimal("1.5"))
    with pytest.raises(ValidationError):
        EstimationProfile(throughput_cm3_per_hour=Decimal(0))
    with pytest.raises(ValidationError):
        EstimationProfile(density_g_per_cm3=Decimal(0))
    with pytest.raises(ValidationError):
        estimate(analyse(), scale=Decimal(0))
