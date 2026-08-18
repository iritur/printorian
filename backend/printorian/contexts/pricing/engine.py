"""The pricing engine.

Pure, deterministic, versioned. No database, no clock, no network, no floats —
enforced by the ADR-0002 import contract, not by good intentions.

**What scales with quantity, and what does not**, stated once here because this is
exactly where V1's two calculators silently disagreed (its web copy multiplied
packaging, shipping and flat labour by quantity; its desktop copy did not):

===========================  ==========================================
per unit, times quantity     material, machine time, supervision labour,
                             post-processing, packaging
once per job                 setup handling, engineering (resize), shipping
derived                      overhead (per print hour), failure buffer,
                             rush, discount, margin
===========================  ==========================================

Order of operations is fixed and tested: production cost, then burden, then
commercial adjustments, then margin last.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing.breakdown import (
    Basis,
    BasisKind,
    Breakdown,
    Category,
    LineItem,
)
from printorian.contexts.pricing.codes import (
    ADJUSTMENT_CUSTOMER_DISCOUNT,
    ADJUSTMENT_RUSH,
    ADJUSTMENT_VOLUME_DISCOUNT,
    MARGIN,
    OVERHEAD,
    RISK_FAILURE_BUFFER,
)
from printorian.contexts.pricing.discounts import effective_percent
from printorian.contexts.pricing.lines import (
    labor_lines,
    logistics_lines,
    machine_lines,
    material_lines,
)
from printorian.contexts.pricing.rates import ENGINE_VERSION, CustomerTier, RateSnapshot
from printorian.contexts.pricing.spec import PriceSpec
from printorian.core.money import Money, sum_money

_HUNDRED = Decimal(100)
_MINUTES_PER_HOUR = Decimal(60)


def price(
    spec: PriceSpec,
    rates: RateSnapshot,
    tier: CustomerTier | None = None,
) -> Breakdown:
    """Price ``spec`` under ``rates``. Same inputs always give the same answer."""
    tier = tier or CustomerTier(code=spec.customer_tier_code)
    lines = _pre_adjustment_lines(spec, rates)

    lines.extend(_adjustment_lines(spec, rates, tier, lines))
    lines.append(_margin_line(rates, tier, lines))

    return Breakdown(
        lines=tuple(_rounded(line) for line in lines),
        currency=rates.currency,
        quantity=spec.quantity,
        engine_version=ENGINE_VERSION,
        rate_snapshot_id=rates.snapshot_id,
    )


# ------------------------------------------------------------------ sections


def _pre_adjustment_lines(spec: PriceSpec, rates: RateSnapshot) -> list[LineItem]:
    """Everything before rush, discounts and margin — the cost of doing the work.

    Split out because the tier-cliff guard needs this same figure evaluated at
    other quantities, and it must be the *same* computation, not a parallel
    approximation of it.
    """
    quantity = Decimal(spec.quantity)
    print_hours_total = (spec.estimate.print_time.minutes / _MINUTES_PER_HOUR) * quantity

    lines: list[LineItem] = []
    lines.extend(material_lines(spec, rates, quantity))
    lines.extend(machine_lines(rates, print_hours_total))
    lines.extend(labor_lines(spec, rates, quantity, print_hours_total))
    lines.extend(logistics_lines(spec, rates, quantity))

    overhead = rates.overhead_per_print_hour * print_hours_total
    if overhead > 0:
        lines.append(
            LineItem(
                code=OVERHEAD,
                category=Category.OVERHEAD,
                amount=rates.money(overhead),
                basis=Basis(
                    kind=BasisKind.RATE_OVER_QUANTITY,
                    quantity=print_hours_total,
                    unit="hour",
                    rate=rates.overhead_per_print_hour,
                ),
            )
        )

    # Failure buffer covers what the farm might have to reprint, so it applies to
    # production cost only — reprinting a part does not re-post the parcel.
    production_codes = tuple(line.code for line in lines if line.category is not Category.LOGISTICS)
    buffer_amount = (
        _sum(lines, production_codes, rates).amount * rates.failure_buffer_percent / _HUNDRED
    )
    if buffer_amount != 0:
        lines.append(
            LineItem(
                code=RISK_FAILURE_BUFFER,
                category=Category.RISK,
                amount=rates.money(buffer_amount),
                basis=Basis(
                    kind=BasisKind.PERCENT_OF,
                    percent=rates.failure_buffer_percent,
                    of_codes=production_codes,
                ),
            )
        )
    return lines


def _base_amount(spec: PriceSpec, rates: RateSnapshot, quantity: int) -> Decimal:
    """Pre-adjustment cost for the same order at a different quantity."""
    at_quantity = spec.with_changes(quantity=quantity)
    lines = _pre_adjustment_lines(at_quantity, rates)
    return _sum(lines, tuple(line.code for line in lines), rates).amount


def _adjustment_lines(
    spec: PriceSpec, rates: RateSnapshot, tier: CustomerTier, lines: list[LineItem]
) -> list[LineItem]:
    """Rush, then discounts — all applied to the same pre-margin cost base."""
    base_codes = tuple(line.code for line in lines)
    base = _sum(lines, base_codes, rates)
    adjustments: list[LineItem] = []

    if spec.rush and rates.rush_surcharge_percent != 0:
        adjustments.append(
            LineItem(
                code=ADJUSTMENT_RUSH,
                category=Category.ADJUSTMENT,
                amount=rates.money(base.amount * rates.rush_surcharge_percent / _HUNDRED),
                basis=Basis(
                    kind=BasisKind.PERCENT_OF,
                    percent=rates.rush_surcharge_percent,
                    of_codes=base_codes,
                ),
            )
        )

    # The percent may be capped below the tier's nominal value to stop a larger
    # order costing less than a smaller one (see .discounts).
    volume_percent, volume_tier = effective_percent(
        spec.quantity,
        rates,
        lambda quantity: _base_amount(spec, rates, quantity),
        rush_fraction=(rates.rush_surcharge_percent / _HUNDRED) if spec.rush else Decimal(0),
        customer_fraction=tier.discount_percent / _HUNDRED,
    )
    if volume_tier is not None and volume_percent > 0:
        adjustments.append(
            LineItem(
                code=ADJUSTMENT_VOLUME_DISCOUNT,
                category=Category.ADJUSTMENT,
                amount=rates.money(-(base.amount * volume_percent / _HUNDRED)),
                basis=Basis(
                    kind=BasisKind.TIERED_PERCENT,
                    # The percent actually applied, which is what the customer is
                    # owed an explanation for.
                    percent=volume_percent,
                    of_codes=base_codes,
                    tier_min_quantity=volume_tier.min_quantity,
                ),
            )
        )

    if tier.discount_percent > 0:
        adjustments.append(
            LineItem(
                code=ADJUSTMENT_CUSTOMER_DISCOUNT,
                category=Category.ADJUSTMENT,
                amount=rates.money(-(base.amount * tier.discount_percent / _HUNDRED)),
                basis=Basis(
                    kind=BasisKind.PERCENT_OF,
                    percent=tier.discount_percent,
                    of_codes=base_codes,
                ),
            )
        )
    return adjustments


def _margin_line(rates: RateSnapshot, tier: CustomerTier, lines: list[LineItem]) -> LineItem:
    """Margin is last, over everything else — including adjustments."""
    percent = (
        tier.margin_percent_override
        if tier.margin_percent_override is not None
        else rates.margin_percent
    )
    base_codes = tuple(line.code for line in lines)
    base = _sum(lines, base_codes, rates)
    return LineItem(
        code=MARGIN,
        category=Category.MARGIN,
        amount=rates.money(base.amount * percent / _HUNDRED),
        basis=Basis(kind=BasisKind.PERCENT_OF, percent=percent, of_codes=base_codes),
    )


# ------------------------------------------------------------------ helpers


def _sum(lines: list[LineItem], codes: tuple[str, ...], rates: RateSnapshot) -> Money:
    wanted = set(codes)
    return sum_money([line.amount for line in lines if line.code in wanted], rates.currency)


def _rounded(line: LineItem) -> LineItem:
    """Round once, at the end.

    Intermediate values stay at full Decimal precision so that percentages
    compound exactly; only the presented figures are quantized. Because every line
    is rounded and the total is their sum, the printed lines always add up to the
    printed total — which is the property customers actually check.
    """
    return LineItem(
        code=line.code,
        category=line.category,
        amount=line.amount.rounded(),
        basis=line.basis,
    )
