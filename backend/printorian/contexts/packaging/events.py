"""Events published by the packing post.

`ParcelShipped` is the one with consequences outside this context: it is what
moves the order to «отправлен» and what tells the customer their parcel is on a
van. Everything else here is the board's reason to refetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from printorian.contexts.packaging.policies import HoldReason, PackStatus
from printorian.core.events import Event
from printorian.core.ids import EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class ParcelRaised(Event):
    """An inspected order has become a parcel for somebody to make."""

    name: ClassVar[str] = "packaging.parcel_raised"

    task_id: EntityId
    order_id: EntityId
    number: str
    items: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ParcelStatusChanged(Event):
    """A parcel moved. The board's one reason to refetch."""

    name: ClassVar[str] = "packaging.parcel_status_changed"

    task_id: EntityId
    order_id: EntityId
    number: str
    from_status: PackStatus
    to_status: PackStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class ParcelHeld(Event):
    """A parcel is stuck on something outside the post.

    Worth its own event because the fix belongs to somebody who is not looking at
    this screen: an unpaid invoice is the finance desk's to clear, and a parcel
    that silently sat in a column until the van left is the failure this prevents.
    """

    name: ClassVar[str] = "packaging.parcel_held"

    task_id: EntityId
    order_id: EntityId
    number: str
    reason: HoldReason


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscrepancyFound(Event):
    """The completeness check disagreed with the order.

    The one event on this screen that means a customer is about to be short a
    part, and the input to the "недовложений" figure the post is judged on.
    """

    name: ClassVar[str] = "packaging.discrepancy_found"

    task_id: EntityId
    order_id: EntityId
    number: str
    discrepancy_code: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ParcelShipped(Event):
    """Handed to the carrier. Carries the weight the customer will be billed on."""

    name: ClassVar[str] = "packaging.parcel_shipped"

    task_id: EntityId
    order_id: EntityId
    number: str
    carrier_code: str
    weight_grams: Decimal


__all__ = [
    "DiscrepancyFound",
    "ParcelHeld",
    "ParcelRaised",
    "ParcelShipped",
    "ParcelStatusChanged",
]
