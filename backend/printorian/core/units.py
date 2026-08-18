"""Physical quantities.

These exist because the units that get silently confused in a print farm are
minutes/hours and grams/kilograms, and because pricing multiplies durations by
hourly rates on almost every line. Each quantity is Decimal-backed and refuses
to mix with a different quantity type.

Subclasses are deliberately *not* re-decorated with ``@dataclass``: that would
generate an ``__init__`` bypassing :func:`_coerce` and let floats back in.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Self

from printorian.core.errors import ValidationError

Numeric = Decimal | int | str

_MINUTES_PER_HOUR = Decimal(60)
_GRAMS_PER_KILOGRAM = Decimal(1000)
_MM_PER_CM = Decimal(10)


def _coerce(value: Numeric) -> Decimal:
    if isinstance(value, float):
        raise ValidationError(
            "error.units.float_forbidden",
            hint="Quantities are Decimal-backed; pass Decimal, int or str.",
        )
    return value if isinstance(value, Decimal) else Decimal(value)


@dataclass(frozen=True, slots=True, order=True)
class _Quantity:
    """Base for single-unit scalar quantities."""

    value: Decimal

    def __init__(self, value: Numeric) -> None:
        object.__setattr__(self, "value", _coerce(value))

    @classmethod
    def zero(cls) -> Self:
        return cls(Decimal(0))

    def __add__(self, other: Self) -> Self:
        self._same_type(other)
        return type(self)(self.value + other.value)

    def __sub__(self, other: Self) -> Self:
        self._same_type(other)
        return type(self)(self.value - other.value)

    def __mul__(self, factor: Numeric) -> Self:
        return type(self)(self.value * _coerce(factor))

    __rmul__ = __mul__

    def __truediv__(self, divisor: Numeric) -> Self:
        d = _coerce(divisor)
        if d == 0:
            raise ValidationError("error.units.division_by_zero")
        return type(self)(self.value / d)

    def _same_type(self, other: Self) -> None:
        if type(self) is not type(other):
            raise ValidationError(
                "error.units.type_mismatch",
                left=type(self).__name__,
                right=type(other).__name__,
            )

    @property
    def unit(self) -> str:
        return ""

    def __str__(self) -> str:
        return f"{self.value} {self.unit}"


class Duration(_Quantity):
    """A span of production time, stored in minutes."""

    __slots__ = ()

    @classmethod
    def from_hours(cls, hours: Numeric) -> Duration:
        return cls(_coerce(hours) * _MINUTES_PER_HOUR)

    @property
    def minutes(self) -> Decimal:
        return self.value

    @property
    def hours(self) -> Decimal:
        """Hours as an exact ratio — the form every hourly rate needs."""
        return self.value / _MINUTES_PER_HOUR

    @property
    def unit(self) -> str:
        return "min"


class Mass(_Quantity):
    """Material mass, stored in grams."""

    __slots__ = ()

    @classmethod
    def from_kilograms(cls, kg: Numeric) -> Mass:
        return cls(_coerce(kg) * _GRAMS_PER_KILOGRAM)

    @property
    def grams(self) -> Decimal:
        return self.value

    @property
    def kilograms(self) -> Decimal:
        return self.value / _GRAMS_PER_KILOGRAM

    @property
    def unit(self) -> str:
        return "g"


class Length(_Quantity):
    """Linear dimension, stored in millimetres."""

    __slots__ = ()

    @classmethod
    def from_centimetres(cls, cm: Numeric) -> Length:
        return cls(_coerce(cm) * _MM_PER_CM)

    @property
    def millimetres(self) -> Decimal:
        return self.value

    @property
    def unit(self) -> str:
        return "mm"


class Volume(_Quantity):
    """Geometric volume, stored in cubic centimetres (the scenario's sell unit)."""

    __slots__ = ()

    @property
    def cubic_centimetres(self) -> Decimal:
        return self.value

    def mass_at(self, density_g_per_cm3: Numeric) -> Mass:
        """Convert to mass using a material density."""
        return Mass(self.value * _coerce(density_g_per_cm3))

    @property
    def unit(self) -> str:
        return "cm3"


class Energy(_Quantity):
    """Electrical energy, stored in kilowatt-hours."""

    __slots__ = ()

    @classmethod
    def from_power_over_time(cls, kilowatts: Numeric, duration: Duration) -> Energy:
        return cls(_coerce(kilowatts) * duration.hours)

    @property
    def kilowatt_hours(self) -> Decimal:
        return self.value

    @property
    def unit(self) -> str:
        return "kWh"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned model extents."""

    x: Length
    y: Length
    z: Length

    def fits_within(self, other: BoundingBox) -> bool:
        """True when this box fits inside ``other`` without rotation."""
        return self.x <= other.x and self.y <= other.y and self.z <= other.z

    def scaled(self, factor: Numeric) -> BoundingBox:
        f = _coerce(factor)
        return BoundingBox(self.x * f, self.y * f, self.z * f)
