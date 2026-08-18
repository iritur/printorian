"""Money — the only sanctioned way to represent an amount of currency.

Rules this type exists to enforce (ADR-0002, ADR-0012):

* Amounts are :class:`~decimal.Decimal`. Constructing from ``float`` raises.
* Arithmetic between different currencies raises rather than silently coercing.
* Rounding is explicit, half-up, to the currency's minor unit.
* Splitting an amount never loses or invents a minor unit (:meth:`Money.allocate`).

V1 mixed ``decimal`` and ``double`` through its pricing stack and produced two
implementations that disagreed. This type makes that class of bug a TypeError.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Self

from printorian.core.errors import ValidationError


class Currency(StrEnum):
    """Supported currencies and, implicitly, their minor-unit exponent."""

    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"

    @property
    def exponent(self) -> int:
        """Number of decimal places in the currency's minor unit."""
        return 2


Numeric = Decimal | int | str


def _coerce(value: Numeric) -> Decimal:
    if isinstance(value, float):
        raise ValidationError(
            "error.money.float_forbidden",
            hint="Construct Money from Decimal, int or str — never float.",
        )
    if isinstance(value, Decimal):
        return value
    return Decimal(value)


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact amount in a single currency."""

    amount: Decimal
    currency: Currency

    def __init__(self, amount: Numeric, currency: Currency = Currency.RUB) -> None:
        object.__setattr__(self, "amount", _coerce(amount))
        object.__setattr__(self, "currency", currency)

    # -- construction ----------------------------------------------------

    @classmethod
    def zero(cls, currency: Currency = Currency.RUB) -> Self:
        return cls(Decimal(0), currency)

    # -- invariants ------------------------------------------------------

    def _same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise ValidationError(
                "error.money.currency_mismatch",
                left=str(self.currency),
                right=str(other.currency),
            )

    # -- arithmetic ------------------------------------------------------

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Numeric) -> Money:
        return Money(self.amount * _coerce(factor), self.currency)

    __rmul__ = __mul__

    def __truediv__(self, divisor: Numeric) -> Money:
        d = _coerce(divisor)
        if d == 0:
            raise ValidationError("error.money.division_by_zero")
        return Money(self.amount / d, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    # -- comparison ------------------------------------------------------

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount >= other.amount

    # -- predicates ------------------------------------------------------

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    # -- rounding and splitting -----------------------------------------

    def rounded(self) -> Money:
        """Round half-up to the currency's minor unit."""
        quantum = Decimal(1).scaleb(-self.currency.exponent)
        return Money(self.amount.quantize(quantum, rounding=ROUND_HALF_UP), self.currency)

    def allocate(self, weights: Sequence[Decimal | int]) -> list[Money]:
        """Split into parts proportional to ``weights``, losing nothing.

        The remainder in minor units is handed out one at a time, largest weight
        first, so the parts always sum back to exactly ``self.rounded()``.
        """
        if not weights:
            raise ValidationError("error.money.allocate_no_weights")
        decimal_weights = [_coerce(w) for w in weights]
        total_weight = sum(decimal_weights, Decimal(0))
        if total_weight <= 0:
            raise ValidationError("error.money.allocate_nonpositive_weights")

        quantum = Decimal(1).scaleb(-self.currency.exponent)
        target = self.rounded().amount
        minor_total = int((target / quantum).to_integral_value(rounding=ROUND_HALF_UP))

        raw = [minor_total * w / total_weight for w in decimal_weights]
        floors = [int(r.to_integral_value(rounding="ROUND_FLOOR")) for r in raw]
        remainder = minor_total - sum(floors)

        # Hand out the remaining minor units to the largest fractional parts.
        order = sorted(
            range(len(raw)),
            key=lambda i: (raw[i] - floors[i], decimal_weights[i]),
            reverse=True,
        )
        for i in order[: abs(remainder)]:
            floors[i] += 1 if remainder > 0 else -1

        return [Money(Decimal(f) * quantum, self.currency) for f in floors]

    # -- representation --------------------------------------------------

    def __str__(self) -> str:
        """Machine representation. Localized formatting belongs to the client."""
        return f"{self.rounded().amount} {self.currency.value}"

    def __repr__(self) -> str:
        return f"Money('{self.amount}', {self.currency.value})"


def sum_money(items: list[Money], currency: Currency = Currency.RUB) -> Money:
    """Total a list of amounts, returning zero in ``currency`` when empty."""
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total
