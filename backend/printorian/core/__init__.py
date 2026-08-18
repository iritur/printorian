"""Foundation layer: config, money, units, ids, time, errors, events.

``core`` depends on no feature module. Everything else may depend on it.

Note that persistence (:mod:`printorian.core.db`) is deliberately **not** re-exported
here. Importing this package must stay free of SQLAlchemy and of the event bus, so
that :mod:`printorian.contexts.pricing` can depend on ``core`` primitives without
breaking the ADR-0002 purity contract. Pure modules import the submodule they need
(``from printorian.core.money import Money``); everything else may use this facade.
"""

from printorian.core.clock import Clock, FixedClock, SystemClock
from printorian.core.config import Environment, Settings, get_settings
from printorian.core.errors import (
    ConfigurationError,
    ConflictError,
    DomainRuleViolationError,
    IntegrationError,
    NotFoundError,
    PermissionDeniedError,
    PrintorianError,
    UnauthenticatedError,
    ValidationError,
)
from printorian.core.events import Event, EventBus, event_bus
from printorian.core.ids import EntityId, new_id
from printorian.core.money import Currency, Money, sum_money
from printorian.core.units import (
    BoundingBox,
    Duration,
    Energy,
    Length,
    Mass,
    Volume,
)

__all__ = [
    "BoundingBox",
    "Clock",
    "ConfigurationError",
    "ConflictError",
    "Currency",
    "DomainRuleViolationError",
    "Duration",
    "Energy",
    "EntityId",
    "Environment",
    "Event",
    "EventBus",
    "FixedClock",
    "IntegrationError",
    "Length",
    "Mass",
    "Money",
    "NotFoundError",
    "PermissionDeniedError",
    "PrintorianError",
    "Settings",
    "SystemClock",
    "UnauthenticatedError",
    "ValidationError",
    "Volume",
    "event_bus",
    "get_settings",
    "new_id",
    "sum_money",
]
