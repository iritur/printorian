"""Pricing — the cost stack and the price of everything.

The contract, enforced by ``.importlinter`` in CI: this package imports
:mod:`printorian.core` primitives and nothing else. No SQLAlchemy, no Redis, no
HTTP, no ``time``, no ``random``. Rates arrive as an immutable :class:`RateSnapshot`
argument; the engine reads no configuration and no clock.

That purity buys three things at once (ARCHITECTURE §5):

* the itemized breakdown the customer sees *is* the engine's return value;
* per-option deltas are ``diff(price(a), price(b))`` — no second implementation;
* a historical order reprices identically, because its rate snapshot is stored
  alongside it.

Import core submodules directly (``from printorian.core.money import Money``)
rather than the ``printorian.core`` facade, which pulls in the event bus.
"""

from printorian.contexts.pricing.breakdown import (
    Basis,
    BasisKind,
    Breakdown,
    BreakdownDelta,
    Category,
    LineDelta,
    LineItem,
)
from printorian.contexts.pricing.codes import (
    ADJUSTMENT_CUSTOMER_DISCOUNT,
    ADJUSTMENT_RUSH,
    ADJUSTMENT_VOLUME_DISCOUNT,
    LABOR_ENGINEERING,
    LABOR_SETUP,
    LABOR_SUPERVISION,
    LOGISTICS_PACKAGING,
    LOGISTICS_SHIPPING,
    MACHINE_DEPRECIATION,
    MACHINE_ELECTRICITY,
    MARGIN,
    MATERIAL,
    MATERIAL_PROCUREMENT,
    MATERIAL_PURGE,
    OVERHEAD,
    POSTPROCESS_PREFIX,
    RISK_FAILURE_BUFFER,
)
from printorian.contexts.pricing.delta import diff, preview
from printorian.contexts.pricing.engine import price
from printorian.contexts.pricing.finishes import FINISH_CATALOGUE
from printorian.contexts.pricing.loyalty import (
    LOYALTY_LADDER,
    LoyaltyStep,
    next_step,
    step_for_spend,
    tier_for_spend,
)
from printorian.contexts.pricing.rates import (
    ENGINE_VERSION,
    CustomerTier,
    DiscountLadder,
    DiscountTier,
    RateSnapshot,
)
from printorian.contexts.pricing.reprice import prepared_cost
from printorian.contexts.pricing.serialization import (
    breakdown_from_dict,
    breakdown_to_dict,
    delta_to_dict,
    rates_from_dict,
    rates_to_dict,
)
from printorian.contexts.pricing.spec import (
    MAX_COLORS,
    EstimateSource,
    FinishOption,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    ScenarioProfile,
)

__all__ = [
    "ADJUSTMENT_CUSTOMER_DISCOUNT",
    "ADJUSTMENT_RUSH",
    "ADJUSTMENT_VOLUME_DISCOUNT",
    "ENGINE_VERSION",
    "FINISH_CATALOGUE",
    "LABOR_ENGINEERING",
    "LABOR_SETUP",
    "LABOR_SUPERVISION",
    "LOGISTICS_PACKAGING",
    "LOGISTICS_SHIPPING",
    "LOYALTY_LADDER",
    "MACHINE_DEPRECIATION",
    "MACHINE_ELECTRICITY",
    "MARGIN",
    "MATERIAL",
    "MATERIAL_PROCUREMENT",
    "MATERIAL_PURGE",
    "MAX_COLORS",
    "OVERHEAD",
    "POSTPROCESS_PREFIX",
    "RISK_FAILURE_BUFFER",
    "Basis",
    "BasisKind",
    "Breakdown",
    "BreakdownDelta",
    "Category",
    "CustomerTier",
    "DiscountLadder",
    "DiscountTier",
    "EstimateSource",
    "FinishOption",
    "LineDelta",
    "LineItem",
    "LoyaltyStep",
    "MaterialPrice",
    "PriceSpec",
    "PrintEstimate",
    "RateSnapshot",
    "ScenarioProfile",
    "breakdown_from_dict",
    "breakdown_to_dict",
    "delta_to_dict",
    "diff",
    "next_step",
    "prepared_cost",
    "preview",
    "price",
    "rates_from_dict",
    "rates_to_dict",
    "step_for_spend",
    "tier_for_spend",
]
