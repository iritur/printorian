"""Events published by the payments context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from printorian.core.events import Event
from printorian.core.ids import EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentStarted(Event):
    name: ClassVar[str] = "payment.started"

    payment_id: EntityId
    order_id: EntityId
    amount: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentSettled(Event):
    name: ClassVar[str] = "payment.settled"

    payment_id: EntityId
    order_id: EntityId
    amount: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentRefunded(Event):
    name: ClassVar[str] = "payment.refunded"

    payment_id: EntityId
    order_id: EntityId
    amount: str
    reason: str
