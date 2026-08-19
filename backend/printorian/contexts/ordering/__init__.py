"""Ordering — what a customer bought, and what was promised about it.

Public interface. The defining rule: an order's price is **pinned** at placement
(the serialized breakdown, plus the rate-snapshot id and engine version that made
it). Nothing here ever reprices an existing order.
"""

from printorian.contexts.ordering.history import (
    MONTHS_SHOWN,
    NOT_REVENUE,
    lifetime,
    lines_per_asset,
    order_numbers,
    spent,
)
from printorian.contexts.ordering.policies import (
    POLICIES,
    TRANSITIONS,
    DecayPolicy,
    DeliveryMethod,
    OrderStatus,
    assert_transition,
    can_transition,
    policy,
)
from printorian.contexts.ordering.promise import (
    MIN_LEAD_HOURS,
    PROMISE_BUFFER_PERCENT,
    RUSH_LEAD_HOURS,
    promised_hours,
)
from printorian.contexts.ordering.schemas import (
    Delivery,
    DraftLine,
    Lifetime,
    MonthPoint,
    OrderEventView,
    OrderLineView,
    OrderTable,
    OrderView,
    PlaceOrder,
    RepriceLine,
    StatusCount,
)
from printorian.contexts.ordering.service import OrderingService

__all__ = [
    "MIN_LEAD_HOURS",
    "MONTHS_SHOWN",
    "NOT_REVENUE",
    "POLICIES",
    "PROMISE_BUFFER_PERCENT",
    "RUSH_LEAD_HOURS",
    "TRANSITIONS",
    "DecayPolicy",
    "Delivery",
    "DeliveryMethod",
    "DraftLine",
    "Lifetime",
    "MonthPoint",
    "OrderEventView",
    "OrderLineView",
    "OrderStatus",
    "OrderTable",
    "OrderView",
    "OrderingService",
    "PlaceOrder",
    "RepriceLine",
    "StatusCount",
    "assert_transition",
    "can_transition",
    "lifetime",
    "lines_per_asset",
    "order_numbers",
    "policy",
    "promised_hours",
    "spent",
]
