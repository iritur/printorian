"""Rates: every number the engine is allowed to use.

A :class:`RateSnapshot` is immutable and **content-addressed** — its id is a hash
of its own values. Store that id on an order and the quote reprices identically
years later, without needing a separate versioning table.

Note the id is a hash rather than a UUID on purpose: :mod:`printorian.core.ids`
imports ``time``, which the ADR-0002 purity contract forbids here. The constraint
pushed toward the better design.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise

from printorian.core.errors import ValidationError
from printorian.core.money import Currency, Money

#: Bumped when the *shape* of the calculation changes, so historical breakdowns
#: are never silently recomputed under new rules. Stored alongside every order.
ENGINE_VERSION = "1.0.0"

_MARGIN_FLOOR_PERCENT = Decimal(-100)


@dataclass(frozen=True, slots=True, order=True)
class DiscountTier:
    """From ``min_quantity`` upward, take ``percent`` off the production subtotal."""

    min_quantity: int
    percent: Decimal

    def __post_init__(self) -> None:
        if self.min_quantity < 1:
            raise ValidationError("error.pricing.tier_min_quantity", value=self.min_quantity)
        if not (Decimal(0) <= self.percent < Decimal(100)):
            raise ValidationError("error.pricing.tier_percent", value=str(self.percent))


@dataclass(frozen=True, slots=True)
class DiscountLadder:
    """Quantity discounts, e.g. "every 10 gets cheaper" from the scenario.

    Validated to be non-inverting: a larger order can never cost more per unit than
    a smaller one, which is the kind of bug that only shows up in an angry email.
    """

    tiers: tuple[DiscountTier, ...] = ()

    def __post_init__(self) -> None:
        ordered = sorted(self.tiers)
        for earlier, later in pairwise(ordered):
            if earlier.min_quantity == later.min_quantity:
                raise ValidationError("error.pricing.tier_duplicate", quantity=earlier.min_quantity)
            if later.percent < earlier.percent:
                raise ValidationError(
                    "error.pricing.ladder_inverts",
                    at_quantity=later.min_quantity,
                    percent=str(later.percent),
                    previous_percent=str(earlier.percent),
                )
        object.__setattr__(self, "tiers", tuple(ordered))

    def percent_for(self, quantity: int) -> Decimal:
        applicable = [tier for tier in self.tiers if quantity >= tier.min_quantity]
        return applicable[-1].percent if applicable else Decimal(0)

    def tier_for(self, quantity: int) -> DiscountTier | None:
        applicable = [tier for tier in self.tiers if quantity >= tier.min_quantity]
        return applicable[-1] if applicable else None


@dataclass(frozen=True, slots=True)
class CustomerTier:
    """A negotiated price book: extra discount and/or a different margin."""

    code: str = "standard"
    discount_percent: Decimal = Decimal(0)
    margin_percent_override: Decimal | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RateSnapshot:
    """Every rate used by one calculation, frozen together.

    Rates are *given* to the engine, never looked up by it. That is what makes the
    engine pure and the result reproducible.
    """

    currency: Currency = Currency.RUB

    # -- labour ----------------------------------------------------------
    labor_rate_per_hour: Decimal = Decimal(600)
    #: Operator hours consumed per hour of printing (watching, restarts, checks).
    labor_hours_per_print_hour: Decimal = Decimal("0.05")
    #: One-off handling per job: load plate, start, remove, clean.
    labor_hours_per_job: Decimal = Decimal("0.25")
    #: Engineering time charged once when a model is rescaled (scenario option 2c).
    engineering_hours_per_resize: Decimal = Decimal("0.5")
    postprocess_rate_per_hour: Decimal = Decimal(500)

    # -- machine ---------------------------------------------------------
    electricity_rate_per_kwh: Decimal = Decimal("6.50")
    printer_power_kw: Decimal = Decimal("0.35")
    #: Amortization per printing hour. Phase 6 replaces this with measured values.
    depreciation_per_printer_hour: Decimal = Decimal(35)

    # -- logistics -------------------------------------------------------
    #: What it costs the farm to buy in a filament it does not stock: courier,
    #: minimum order, and the handling around both. Charged once per order.
    #:
    #: **A placeholder until the farm sets its own.** Like every rate here it is
    #: configuration, and it is pinned into a `RateSnapshot` per order, so
    #: changing it never re-prices work already quoted.
    material_procurement_flat: Decimal = Decimal(500)
    packaging_per_unit: Decimal = Decimal(40)
    shipping_flat: Decimal = Decimal(400)

    # -- burden ----------------------------------------------------------
    overhead_per_print_hour: Decimal = Decimal(25)
    #: Expected scrap. Applied to production cost only, never to shipping.
    failure_buffer_percent: Decimal = Decimal(7)

    # -- commercial ------------------------------------------------------
    rush_surcharge_percent: Decimal = Decimal(25)
    margin_percent: Decimal = Decimal(30)
    #: Charged per additional colour: AMS purges filament on every tool change.
    multicolor_purge_grams_per_extra_color: Decimal = Decimal(12)

    discounts: DiscountLadder = field(default_factory=DiscountLadder)
    #: Cap tier discounts so a larger order can never cost less in total than a
    #: smaller one. See :mod:`printorian.contexts.pricing.discounts`. Turn off for
    #: raw step tiers, accepting that e.g. 50 units may undercut 49.
    guard_tier_cliffs: bool = True

    def __post_init__(self) -> None:
        for name in (
            "labor_rate_per_hour",
            "electricity_rate_per_kwh",
            "printer_power_kw",
            "depreciation_per_printer_hour",
            "postprocess_rate_per_hour",
            "material_procurement_flat",
            "packaging_per_unit",
            "shipping_flat",
            "overhead_per_print_hour",
        ):
            if getattr(self, name) < 0:
                raise ValidationError("error.pricing.negative_rate", rate=name)
        # At -100% the price would be zero; below it the farm pays the customer.
        if self.margin_percent <= _MARGIN_FLOOR_PERCENT:
            raise ValidationError("error.pricing.margin_percent", value=str(self.margin_percent))

    def money(self, amount: Decimal) -> Money:
        return Money(amount, self.currency)

    @property
    def snapshot_id(self) -> str:
        """Stable content hash. Identical rates always produce the same id."""
        parts: list[str] = []
        for name in sorted(self.__slots__):
            value = getattr(self, name)
            if isinstance(value, DiscountLadder):
                parts.append(
                    f"{name}=" + ";".join(f"{t.min_quantity}:{t.percent}" for t in value.tiers)
                )
            else:
                parts.append(f"{name}={value}")
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        return f"rates_{digest[:32]}"
