"""Per-section line builders.

One function per part of the cost stack, kept apart from the orchestration in
:mod:`printorian.contexts.pricing.engine` so that neither file grows into the
1,096-line pricing service V1 ended up with.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing.breakdown import Basis, BasisKind, Category, LineItem
from printorian.contexts.pricing.codes import (
    LABOR_ENGINEERING,
    LABOR_SETUP,
    LABOR_SUPERVISION,
    LOGISTICS_PACKAGING,
    LOGISTICS_SHIPPING,
    MACHINE_DEPRECIATION,
    MACHINE_ELECTRICITY,
    MATERIAL,
    MATERIAL_PROCUREMENT,
    MATERIAL_PURGE,
    POSTPROCESS_PREFIX,
)
from printorian.contexts.pricing.rates import RateSnapshot
from printorian.contexts.pricing.spec import PriceSpec


def material_lines(spec: PriceSpec, rates: RateSnapshot, quantity: Decimal) -> list[LineItem]:
    grams_each = spec.estimate.material_mass.grams
    lines = [
        LineItem(
            code=MATERIAL,
            category=Category.MATERIAL,
            amount=rates.money(grams_each * quantity * spec.material.price_per_gram),
            basis=Basis(
                kind=BasisKind.RATE_OVER_QUANTITY,
                quantity=grams_each * quantity,
                unit="gram",
                rate=spec.material.price_per_gram,
            ),
        )
    ]

    # Every extra colour means a tool change per layer, and the AMS purges filament
    # on each one. Charging it explicitly is why multicolour looks expensive here
    # rather than quietly eating the margin.
    if spec.extra_colors:
        purge_grams = (
            rates.multicolor_purge_grams_per_extra_color * Decimal(spec.extra_colors) * quantity
        )
        lines.append(
            LineItem(
                code=MATERIAL_PURGE,
                category=Category.MATERIAL,
                amount=rates.money(purge_grams * spec.material.price_per_gram),
                basis=Basis(
                    kind=BasisKind.RATE_OVER_QUANTITY,
                    quantity=purge_grams,
                    unit="gram",
                    rate=spec.material.price_per_gram,
                ),
            )
        )

    # A filament the farm does not hold has to be bought in before the plate can
    # run. Charged **once per order rather than per unit**: one delivery covers
    # the whole plate however many copies it makes, so multiplying it by quantity
    # would overcharge a customer for ordering more.
    if spec.material.needs_procurement:
        lines.append(
            LineItem(
                code=MATERIAL_PROCUREMENT,
                category=Category.MATERIAL,
                amount=rates.money(rates.material_procurement_flat),
                basis=Basis(kind=BasisKind.FLAT, quantity=Decimal(1), unit="piece"),
            )
        )
    return lines


def machine_lines(rates: RateSnapshot, print_hours_total: Decimal) -> list[LineItem]:
    return [
        LineItem(
            code=MACHINE_ELECTRICITY,
            category=Category.MACHINE,
            amount=rates.money(
                print_hours_total * rates.printer_power_kw * rates.electricity_rate_per_kwh
            ),
            basis=Basis(
                kind=BasisKind.RATE_OVER_QUANTITY,
                quantity=print_hours_total * rates.printer_power_kw,
                unit="kwh",
                rate=rates.electricity_rate_per_kwh,
            ),
        ),
        LineItem(
            code=MACHINE_DEPRECIATION,
            category=Category.MACHINE,
            amount=rates.money(print_hours_total * rates.depreciation_per_printer_hour),
            basis=Basis(
                kind=BasisKind.RATE_OVER_QUANTITY,
                quantity=print_hours_total,
                unit="hour",
                rate=rates.depreciation_per_printer_hour,
            ),
        ),
    ]


def labor_lines(
    spec: PriceSpec, rates: RateSnapshot, quantity: Decimal, print_hours_total: Decimal
) -> list[LineItem]:
    supervision_hours = print_hours_total * rates.labor_hours_per_print_hour
    lines = [
        LineItem(
            code=LABOR_SUPERVISION,
            category=Category.LABOR,
            amount=rates.money(supervision_hours * rates.labor_rate_per_hour),
            basis=Basis(
                kind=BasisKind.RATE_OVER_QUANTITY,
                quantity=supervision_hours,
                unit="hour",
                rate=rates.labor_rate_per_hour,
            ),
        ),
        LineItem(
            code=LABOR_SETUP,
            category=Category.LABOR,
            amount=rates.money(rates.labor_hours_per_job * rates.labor_rate_per_hour),
            basis=Basis(
                kind=BasisKind.RATE_OVER_QUANTITY,
                quantity=rates.labor_hours_per_job,
                unit="hour",
                rate=rates.labor_rate_per_hour,
            ),
        ),
    ]

    # Resizing is done once for the model, however many copies are printed.
    if spec.is_resized:
        lines.append(
            LineItem(
                code=LABOR_ENGINEERING,
                category=Category.LABOR,
                amount=rates.money(rates.engineering_hours_per_resize * rates.labor_rate_per_hour),
                basis=Basis(
                    kind=BasisKind.RATE_OVER_QUANTITY,
                    quantity=rates.engineering_hours_per_resize,
                    unit="hour",
                    rate=rates.labor_rate_per_hour,
                ),
            )
        )

    for finish in spec.finishes:
        amount = (finish.labor_hours * rates.postprocess_rate_per_hour + finish.flat_fee) * quantity
        lines.append(
            LineItem(
                code=f"{POSTPROCESS_PREFIX}{finish.code}",
                category=Category.LABOR,
                amount=rates.money(amount),
                basis=Basis(
                    kind=BasisKind.PER_UNIT,
                    quantity=quantity,
                    unit="unit",
                    rate=finish.labor_hours * rates.postprocess_rate_per_hour + finish.flat_fee,
                ),
            )
        )
    return lines


def logistics_lines(spec: PriceSpec, rates: RateSnapshot, quantity: Decimal) -> list[LineItem]:
    lines = [
        LineItem(
            code=LOGISTICS_PACKAGING,
            category=Category.LOGISTICS,
            amount=rates.money(rates.packaging_per_unit * quantity),
            basis=Basis(
                kind=BasisKind.PER_UNIT,
                quantity=quantity,
                unit="unit",
                rate=rates.packaging_per_unit,
            ),
        )
    ]
    # One order ships once, so shipping is flat. Collection means no line at all
    # rather than a zero line, so the customer does not wonder what it is.
    if spec.include_shipping:
        lines.append(
            LineItem(
                code=LOGISTICS_SHIPPING,
                category=Category.LOGISTICS,
                amount=rates.money(rates.shipping_flat),
                basis=Basis(kind=BasisKind.FLAT, rate=rates.shipping_flat),
            )
        )
    return lines
