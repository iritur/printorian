"""Mesh heuristic: geometry in, print time and filament mass out.

This is what the customer's price is built on *before* anyone slices (ADR-0006), so
it is deliberately conservative and deliberately explainable. It is also expected to
be wrong: ADR-0013 records every variance against the eventual sliced truth, and
Phase 6 turns those recordings into a calibration report.

The model, in one line: material is the shell plus a fraction of the interior, and
time is that material divided by a throughput rate, plus a fixed setup.

It does not model supports, bridging, ironing, or per-layer travel. Those are the
slicer's job. Pretending otherwise would produce a number that looks authoritative
and is not — which is exactly the failure mode this project is trying to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from printorian.contexts.catalog.mesh import MeshAnalysis
from printorian.core.errors import ValidationError
from printorian.core.units import Duration, Mass, Volume

_MM3_PER_CM3 = Decimal(1000)


@dataclass(frozen=True, slots=True, kw_only=True)
class EstimationProfile:
    """Tunables for the heuristic. Calibrated from measured jobs in Phase 6."""

    #: Fraction of the interior actually filled. 0.15 is a common default.
    infill_fraction: Decimal = Decimal("0.15")
    #: Perimeter shell thickness in millimetres (roughly walls x line width).
    shell_thickness_mm: Decimal = Decimal("1.2")
    #: Filament laid down per hour by a typical FDM machine, in cm3.
    throughput_cm3_per_hour: Decimal = Decimal("14")
    #: Fixed per-job time: heat-up, bed levelling, purge, cool-down.
    setup_minutes: Decimal = Decimal(12)
    #: Grams per cm3. PLA is ~1.24; overridden per material by the caller.
    density_g_per_cm3: Decimal = Decimal("1.24")
    #: Multiplier applied at the end, so a farm can bias estimates from experience.
    calibration_factor: Decimal = Decimal("1.0")

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.infill_fraction <= Decimal(1)):
            raise ValidationError("error.catalog.infill_fraction", value=str(self.infill_fraction))
        if self.throughput_cm3_per_hour <= 0:
            raise ValidationError("error.catalog.throughput")
        if self.density_g_per_cm3 <= 0:
            raise ValidationError("error.catalog.density")


@dataclass(frozen=True, slots=True, kw_only=True)
class PrintPrediction:
    """A predicted print, with the material split kept visible.

    Deliberately not a pricing type: the catalogue measures manufacturing, and the
    caller composes a pricing input from it. Keeps the two contexts independent.
    """

    print_time: Duration
    material_mass: Mass
    shell_volume: Volume
    infill_volume: Volume
    profile: EstimationProfile

    @property
    def extruded_volume(self) -> Volume:
        return Volume(self.shell_volume.cubic_centimetres + self.infill_volume.cubic_centimetres)


def estimate(
    analysis: MeshAnalysis,
    profile: EstimationProfile | None = None,
    *,
    scale: Decimal = Decimal(1),
) -> PrintPrediction:
    """Predict print time and filament mass for one copy of this model.

    ``scale`` is a linear factor: volume grows with its cube, which is why resizing
    a model changes the price far more than customers expect.
    """
    if not analysis.is_priceable:
        raise ValidationError(
            "error.catalog.mesh_not_priceable", watertight=str(analysis.is_watertight)
        )
    if scale <= 0:
        raise ValidationError("error.catalog.scale", value=str(scale))

    settings = profile or EstimationProfile()

    volume_cm3 = analysis.volume.cubic_centimetres * (scale**3)
    area_cm2 = (analysis.surface_area_mm2 * scale * scale) / Decimal(100)

    # Shell is the surface area times its thickness, capped at the solid volume so
    # a thin-walled model never estimates more material than a solid one would.
    shell_cm3 = min(area_cm2 * (settings.shell_thickness_mm / Decimal(10)), volume_cm3)
    interior_cm3 = max(Decimal(0), volume_cm3 - shell_cm3)
    infill_cm3 = interior_cm3 * settings.infill_fraction

    extruded_cm3 = (shell_cm3 + infill_cm3) * settings.calibration_factor
    grams = extruded_cm3 * settings.density_g_per_cm3
    minutes = (extruded_cm3 / settings.throughput_cm3_per_hour) * Decimal(
        60
    ) + settings.setup_minutes

    return PrintPrediction(
        print_time=Duration(_round(minutes, 2)),
        material_mass=Mass(_round(grams, 2)),
        shell_volume=Volume(_round(shell_cm3, 4)),
        infill_volume=Volume(_round(infill_cm3, 4)),
        profile=settings,
    )


def volume_to_mass(volume: Volume, density_g_per_cm3: Decimal) -> Mass:
    return Mass(_round(volume.cubic_centimetres * density_g_per_cm3, 2))


def mm3_to_cm3(value: Decimal) -> Volume:
    return Volume(value / _MM3_PER_CM3)


def _round(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places))
