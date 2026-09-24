"""DTOs crossing the logistics boundary.

**No field here carries money.** The kit's carrier table draws «Средняя цена»
and its shipment detail draws «Что и почём»; both are `VIEW_FINANCIALS` reads
that belong on a route of their own, and a response the packing floor can read
must not quietly start carrying rubles (CLAUDE.md §1). What leaves here is
counts, days and shares.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.logistics.policies import EventKind, EventSource, ShipmentStatus
from printorian.core.ids import EntityId


class ShipmentEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    at: datetime
    kind: EventKind
    source: EventSource
    note: str | None = None
    recorded_by: EntityId | None = None


class ShipmentView(BaseModel):
    """One parcel as the board and the detail read it."""

    id: EntityId
    order_id: EntityId
    order_number: str
    pack_task_id: EntityId | None = None
    carrier_code: str
    #: ``None`` is "no zone claimed the postcode" — a transit time, no promise.
    zone_code: str | None = None
    promised_days: int | None = None
    tracking_number: str | None = None
    status: ShipmentStatus
    shipped_at: datetime
    delivered_at: datetime | None = None
    returned_at: datetime | None = None
    #: Door to door, once delivered. ``None`` while the parcel is out.
    transit_days: Decimal | None = None
    #: Kept its pinned promise. ``None`` while out, and ``None`` for a delivered
    #: parcel that carried no promise — not `false`, which would be "late".
    on_time: bool | None = None
    #: Days since dispatch as of the read, for the «В пути» card.
    days_out: Decimal
    events: list[ShipmentEventView] = Field(default_factory=list)


class LogisticsBoard(BaseModel):
    """The kit's lanes past the packing post: out, in trouble, and lately arrived."""

    at: datetime
    in_transit: list[ShipmentView] = Field(default_factory=list)
    problems: list[ShipmentView] = Field(default_factory=list)
    #: Delivered or returned at or after `closed_since`.
    closed: list[ShipmentView] = Field(default_factory=list)
    closed_since: datetime


class CarrierScore(BaseModel):
    """One row of «Перевозчики», computed from shipments — never typed in.

    «Средняя цена» and «Оценка» are absent: the first is money, the second a
    composite whose weights nobody chose. «Повреждений» is a count of recorded
    `DAMAGED` events, which is what it is — a farm that never records damage
    reads `0` here, and that is a fact about the record, stated as such.
    """

    carrier_code: str
    shipments: int = 0
    delivered: int = 0
    #: Of the delivered, the ones that carried a promise: the denominator of `on_time`.
    promised: int = 0
    on_time: int = 0
    on_time_share: Decimal | None = None
    damaged: int = 0
    returned: int = 0
    #: Mean door-to-door days over the delivered, one place. ``None`` with none.
    mean_transit_days: Decimal | None = None


class ZoneAccuracy(BaseModel):
    """One row of «Сроки доставки»: promised against measured, per zone and promise.

    Grouped by the *pinned* promise rather than by the zone alone. A zone whose
    transit days were raised mid-window appears twice — two different promises,
    each measured against the deliveries that were made under it.
    """

    zone_code: str
    promised_days: int
    delivered: int = 0
    on_time: int = 0
    accuracy: Decimal | None = None
    mean_transit_days: Decimal | None = None


class RecordEvent(BaseModel):
    """A person recording what happened to a parcel, or relaying what the carrier said."""

    kind: EventKind
    source: EventSource = EventSource.PERSON
    #: Defaults to the clock. The carrier's own scan time when a person has it.
    at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class SetTracking(BaseModel):
    tracking_number: str = Field(min_length=1, max_length=80)


__all__ = [
    "CarrierScore",
    "LogisticsBoard",
    "RecordEvent",
    "SetTracking",
    "ShipmentEventView",
    "ShipmentView",
    "ZoneAccuracy",
]
