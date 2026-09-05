"""Turning an already-measured line into a pricing input.

The configurator measures a mesh once. Everything after that — placing the order,
and the checkout re-pricing when the customer picks a courier — works from the
*estimate*, not from the geometry, so neither has to re-upload a file to find out
what a different delivery costs.

Extracted because two call sites needed it and ADR-0002 allows exactly one way to
build a price. A second, subtly different spec assembly is how a checkout ends up
quoting one number and an order charging another.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import InventoryService
from printorian.contexts.ordering import DraftLine
from printorian.contexts.pricing import (
    FinishOption,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
)
from printorian.core.units import Duration, Mass


async def spec_for(
    db: AsyncSession,
    line: DraftLine,
    *,
    include_shipping: bool,
    finishes: Mapping[str, FinishOption],
) -> PriceSpec:
    """Build the pricing input for one configured line.

    ``include_shipping`` is the caller's, because only the caller knows the
    delivery choice — and collection is the *absence* of the service rather than a
    discount on it, so the engine omits the line entirely rather than zeroing it.

    ``finishes`` is the caller's for the same reason ``rates`` and ``tiers`` are:
    resolved at the read edge and handed in, so nothing below this line reaches for
    a settings table (ADR-0002). It used to be the module constant, which meant the
    configurator could quote a farm's own norm-hours while the order charged the
    code default — the "checkout quotes one number and the order charges another"
    failure this module's docstring is written about.
    """
    material = await InventoryService(db).get_by_code(line.material_code)
    return PriceSpec(
        estimate=PrintEstimate(
            print_time=Duration(line.estimated_minutes),
            material_mass=Mass(line.estimated_grams),
        ),
        material=MaterialPrice(
            spec_code=material.code, price_per_gram=material.sell_price_per_gram
        ),
        quantity=line.quantity,
        colors=tuple(line.colors) if line.colors else ("default",),
        scale=line.scale,
        # A code the catalogue no longer prices costs nothing rather than 422-ing,
        # and that asymmetry with the quoting edges is deliberate: this builds the
        # spec for an order that *already exists*, and refusing it would mean an
        # owner tidying the catalogue could stop a placed order from repricing.
        # The quoting edges refuse the same code with `error.pricing.unknown_finish`,
        # because there the customer is still choosing.
        finishes=tuple(finishes.get(code, FinishOption(code=code)) for code in line.finishes),
        rush=line.rush,
        include_shipping=include_shipping,
    )
