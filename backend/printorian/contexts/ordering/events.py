"""Events published by the ordering context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from printorian.core.events import Event
from printorian.core.ids import EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderPlaced(Event):
    name: ClassVar[str] = "order.placed"

    order_id: EntityId
    number: str
    total: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderStatusChanged(Event):
    name: ClassVar[str] = "order.status_changed"

    order_id: EntityId
    number: str
    from_status: str
    to_status: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SlaCreditAccrued(Event):
    """A promise was missed and the customer is now owed money.

    Worth an event rather than a silent field update: management wants to know the
    moment lateness starts costing, not at the end of the month.
    """

    name: ClassVar[str] = "order.sla_credit_accrued"

    order_id: EntityId
    number: str
    credit: str
