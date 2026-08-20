"""Ordering — what a customer bought, and what was promised about it.

Public interface. The defining rule: an order's price is **pinned** at placement
(the serialized breakdown, plus the rate-snapshot id and engine version that made
it). Nothing here ever reprices an existing order.
"""

from printorian.contexts.ordering.finance import (
    CategorySpend,
    DayRevenue,
    FinanceOverview,
    finance_overview,
)
from printorian.contexts.ordering.measures import (
    Period,
    Trend,
    Window,
    month_window,
    window_for,
)
from printorian.contexts.ordering.overview import (
    OrdersOverview,
    StatusSlice,
    numbers_for,
    orders_overview,
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
    "POLICIES",
    "PROMISE_BUFFER_PERCENT",
    "RUSH_LEAD_HOURS",
    "TRANSITIONS",
    "CategorySpend",
    "DayRevenue",
    "DecayPolicy",
    "Delivery",
    "DeliveryMethod",
    "DraftLine",
    "FinanceOverview",
    "OrderEventView",
    "OrderLineView",
    "OrderStatus",
    "OrderTable",
    "OrderView",
    "OrderingService",
    "OrdersOverview",
    "Period",
    "PlaceOrder",
    "RepriceLine",
    "StatusCount",
    "StatusSlice",
    "Trend",
    "Window",
    "assert_transition",
    "can_transition",
    "finance_overview",
    "month_window",
    "numbers_for",
    "orders_overview",
    "policy",
    "promised_hours",
    "window_for",
]
