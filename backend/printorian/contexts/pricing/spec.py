"""The engine's input: everything that affects a price.

One rule keeps this honest — if a value changes the price, it belongs in the spec.
Nothing is read from configuration, the database or the clock inside the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from printorian.core.colors import extra_colors as count_extra_colors
from printorian.core.errors import ValidationError
from printorian.core.units import Duration, Mass

MAX_COLORS = 4  # AMS slots addressable on one plate (scenario option 2b)


class EstimateSource(StrEnum):
    """Where the print time and mass came from.

    The progression that ADR-0013 hangs on: a customer is quoted from a mesh
    heuristic, the truth arrives when an engineer slices, and only then can the
    variance be judged.
    """

    MESH_HEURISTIC = "mesh_heuristic"
    PREPARED_PLATE = "prepared_plate"
    MEASURED = "measured"

    @property
    def is_authoritative(self) -> bool:
        return self is not EstimateSource.MESH_HEURISTIC


@dataclass(frozen=True, slots=True, kw_only=True)
class PrintEstimate:
    """Per-unit print time and filament mass."""

    print_time: Duration
    material_mass: Mass
    source: EstimateSource = EstimateSource.MESH_HEURISTIC

    def __post_init__(self) -> None:
        if self.print_time.minutes <= 0:
            raise ValidationError("error.pricing.print_time")
        if self.material_mass.grams <= 0:
            raise ValidationError("error.pricing.material_mass")


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialPrice:
    """What the chosen material costs, as the catalogue records it."""

    spec_code: str
    price_per_gram: Decimal
    #: True when the farm holds none of this filament and must buy it in.
    #:
    #: A fact about stock, decided by whoever builds the spec — the engine stays
    #: pure and never asks an inventory table anything (ADR-0002).
    needs_procurement: bool = False

    def __post_init__(self) -> None:
        if self.price_per_gram < 0:
            raise ValidationError("error.pricing.material_price", code=self.spec_code)


@dataclass(frozen=True, slots=True, kw_only=True)
class FinishOption:
    """One post-production option (scenario option 2e).

    Priced as labour plus an optional flat fee, so the breakdown can explain a
    finish rather than showing an opaque surcharge.
    """

    code: str
    labor_hours: Decimal = Decimal(0)
    flat_fee: Decimal = Decimal(0)
    #: Calendar days this finish adds — feeds the SLA promise, not the price.
    extra_days: int = 0

    def __post_init__(self) -> None:
        if self.labor_hours < 0 or self.flat_fee < 0:
            raise ValidationError("error.pricing.finish_negative", code=self.code)


@dataclass(frozen=True, slots=True, kw_only=True)
class PriceSpec:
    """A fully-specified thing to price."""

    estimate: PrintEstimate
    material: MaterialPrice
    quantity: int = 1
    #: One entry per colour. More than one implies AMS tool changes and purge waste.
    colors: tuple[str, ...] = ("default",)
    #: 1 means original size; anything else bills engineering time once.
    scale: Decimal = Decimal(1)
    finishes: tuple[FinishOption, ...] = ()
    rush: bool = False
    #: False for customer collection — no shipping line at all.
    include_shipping: bool = True
    customer_tier_code: str = "standard"

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValidationError("error.pricing.quantity", value=self.quantity)
        if not self.colors:
            raise ValidationError("error.pricing.no_colors")
        if len(self.colors) > MAX_COLORS:
            raise ValidationError(
                "error.pricing.too_many_colors", count=len(self.colors), maximum=MAX_COLORS
            )
        if self.scale <= 0:
            raise ValidationError("error.pricing.scale", value=str(self.scale))
        codes = [finish.code for finish in self.finishes]
        if len(codes) != len(set(codes)):
            raise ValidationError("error.pricing.duplicate_finish")

    @property
    def extra_colors(self) -> int:
        """Filament changes, not slots — see `core.colors`."""
        return count_extra_colors(self.colors)

    @property
    def is_resized(self) -> bool:
        return self.scale != Decimal(1)

    def with_changes(self, **changes: object) -> PriceSpec:
        """A copy with fields replaced — how the delta preview builds its 'after'.

        ``dataclasses.replace`` is avoided so that mistyped field names raise here
        rather than silently producing an unchanged spec.
        """
        current = {name: getattr(self, name) for name in self.__slots__}
        unknown = set(changes) - set(current)
        if unknown:
            raise ValidationError("error.pricing.unknown_field", fields=sorted(unknown))
        current.update(changes)
        return PriceSpec(**current)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScenarioProfile:
    """A usage scenario the customer picks instead of naming a material.

    Scenario option 2a: "usage scenario from dialog (system will choose the most
    appropriate materials itself)". The profile states the requirement; the
    inventory context matches it against what is actually in stock.
    """

    code: str
    min_tensile_mpa: Decimal | None = None
    min_hdt_c: Decimal | None = None
    requires_flexible: bool = False
    requires_outdoor: bool = False
    food_safe: bool = False
    preferred_families: tuple[str, ...] = field(default_factory=tuple)
