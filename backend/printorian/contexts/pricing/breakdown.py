"""The engine's output: an itemized, explainable price.

The scenario asks for a *transparent price structure*, so the breakdown is the
return value rather than an afterthought — and each line carries the **basis** on
which it was computed.

The basis is structured data, not a sentence. ADR-0012 forbids the backend from
emitting localized prose, so a line says "0.05 h/print-hour x 4.2 print-hours at
600/hour" as fields; the client renders that as Russian or English text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from printorian.core.errors import ValidationError
from printorian.core.money import Currency, Money, sum_money


class Category(StrEnum):
    """What kind of cost a line represents. Drives grouping in the UI."""

    MATERIAL = "material"
    MACHINE = "machine"
    LABOR = "labor"
    LOGISTICS = "logistics"
    OVERHEAD = "overhead"
    RISK = "risk"
    ADJUSTMENT = "adjustment"
    MARGIN = "margin"


class BasisKind(StrEnum):
    """How a line's amount was arrived at."""

    FLAT = "flat"
    PER_UNIT = "per_unit"
    RATE_OVER_QUANTITY = "rate_over_quantity"
    PERCENT_OF = "percent_of"
    TIERED_PERCENT = "tiered_percent"


@dataclass(frozen=True, slots=True, kw_only=True)
class Basis:
    """Why a line item is the amount it is. Rendered by the client, never here."""

    kind: BasisKind
    quantity: Decimal | None = None
    unit: str | None = None
    rate: Decimal | None = None
    percent: Decimal | None = None
    #: For PERCENT_OF: the codes this percentage was taken over.
    of_codes: tuple[str, ...] = ()
    #: For TIERED_PERCENT: which quantity threshold applied.
    tier_min_quantity: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LineItem:
    """One row of the price structure."""

    code: str
    category: Category
    amount: Money
    basis: Basis

    @property
    def is_credit(self) -> bool:
        return self.amount.is_negative


@dataclass(frozen=True, slots=True, kw_only=True)
class Breakdown:
    """A complete, reproducible price.

    ``rate_snapshot_id`` and ``engine_version`` are stored with the order so the
    exact figure can be recomputed later — the thing V1 could not do, because its
    two calculators disagreed and neither recorded what it had used.
    """

    lines: tuple[LineItem, ...]
    currency: Currency
    quantity: int
    engine_version: str
    rate_snapshot_id: str

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValidationError("error.pricing.quantity", value=self.quantity)
        seen: set[str] = set()
        for line in self.lines:
            if line.code in seen:
                raise ValidationError("error.pricing.duplicate_line", code=line.code)
            seen.add(line.code)
            if line.amount.currency is not self.currency:
                raise ValidationError("error.pricing.line_currency", code=line.code)

    # -- totals ----------------------------------------------------------

    @property
    def total(self) -> Money:
        """What the customer pays. Always exactly the sum of the lines."""
        return sum_money([line.amount for line in self.lines], self.currency).rounded()

    @property
    def unit_price(self) -> Money:
        return (self.total / self.quantity).rounded()

    @property
    def cost(self) -> Money:
        """Everything except margin — what the job costs the farm."""
        return sum_money(
            [line.amount for line in self.lines if line.category is not Category.MARGIN],
            self.currency,
        ).rounded()

    @property
    def margin(self) -> Money:
        return sum_money(
            [line.amount for line in self.lines if line.category is Category.MARGIN],
            self.currency,
        ).rounded()

    def by_category(self) -> dict[Category, Money]:
        totals: dict[Category, Money] = {}
        for line in self.lines:
            current = totals.get(line.category, Money.zero(self.currency))
            totals[line.category] = current + line.amount
        return {category: amount.rounded() for category, amount in totals.items()}

    def line(self, code: str) -> LineItem | None:
        return next((line for line in self.lines if line.code == code), None)

    def amount_of(self, code: str) -> Money:
        found = self.line(code)
        return found.amount if found else Money.zero(self.currency)


@dataclass(frozen=True, slots=True, kw_only=True)
class LineDelta:
    """How one line changed between two priced configurations."""

    code: str
    category: Category
    before: Money
    after: Money

    @property
    def change(self) -> Money:
        return (self.after - self.before).rounded()

    @property
    def is_new(self) -> bool:
        return self.before.is_zero and not self.after.is_zero

    @property
    def is_removed(self) -> bool:
        return not self.before.is_zero and self.after.is_zero


@dataclass(frozen=True, slots=True, kw_only=True)
class BreakdownDelta:
    """The scenario's option preview: "+120 in labor, -260 in material".

    Produced by pricing the same order twice and subtracting. There is no second
    code path that could disagree with the first.
    """

    lines: tuple[LineDelta, ...]
    currency: Currency
    total_before: Money
    total_after: Money
    #: The same comparison per item.
    #:
    #: Not derivable from the totals by the client: quantity itself can be the
    #: thing that changed, so "total change divided by quantity" would divide by
    #: the wrong number exactly when the answer matters most. Carried explicitly so
    #: an option can be labelled «+ 340 ₽ / шт» without anyone dividing money.
    unit_before: Money
    unit_after: Money
    comparable: bool = field(default=True)

    @property
    def total_change(self) -> Money:
        return (self.total_after - self.total_before).rounded()

    @property
    def unit_change(self) -> Money:
        return (self.unit_after - self.unit_before).rounded()

    @property
    def changed(self) -> tuple[LineDelta, ...]:
        """Only the lines that actually moved — what the UI shows."""
        return tuple(line for line in self.lines if not line.change.is_zero)

    @property
    def increases(self) -> tuple[LineDelta, ...]:
        return tuple(line for line in self.changed if not line.change.is_negative)

    @property
    def decreases(self) -> tuple[LineDelta, ...]:
        return tuple(line for line in self.changed if line.change.is_negative)
