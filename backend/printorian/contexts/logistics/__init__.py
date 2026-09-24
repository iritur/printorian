"""Logistics — the parcel after the post: carrier, zone, tracking, arrival.

Public interface. Two rules shape it.

**A shipment is the record of a promise and of what happened to it.** The zone
and its transit days are pinned on the row when the parcel leaves, from the
order's own rate snapshot, and every later change of state is an event nobody
overwrites. «В срок» and «Точность» are computed from those rows and are never
typed in; a zone edited afterwards moves the next promise and never this one.

**Counts, days and shares leave here; money does not.** «Средняя цена» and «Что
и почём» are `VIEW_FINANCIALS` reads on routes of their own, and a response the
packing floor can read must not quietly start carrying rubles (CLAUDE.md §1).
"""

from printorian.contexts.logistics.models import Shipment, ShipmentEvent
from printorian.contexts.logistics.policies import (
    STATUS_AFTER,
    EventKind,
    EventSource,
    ShipmentStatus,
    assert_open,
    on_time,
    transit_days,
)
from printorian.contexts.logistics.reads import (
    carrier_scores,
    damaged_counts,
    shipments_since,
    zone_accuracy,
)
from printorian.contexts.logistics.schemas import (
    CarrierScore,
    LogisticsBoard,
    RecordEvent,
    SetTracking,
    ShipmentEventView,
    ShipmentView,
    ZoneAccuracy,
)
from printorian.contexts.logistics.service import LogisticsService, view_of

__all__ = [
    "STATUS_AFTER",
    "CarrierScore",
    "EventKind",
    "EventSource",
    "LogisticsBoard",
    "LogisticsService",
    "RecordEvent",
    "SetTracking",
    "Shipment",
    "ShipmentEvent",
    "ShipmentEventView",
    "ShipmentStatus",
    "ShipmentView",
    "ZoneAccuracy",
    "assert_open",
    "carrier_scores",
    "damaged_counts",
    "on_time",
    "shipments_since",
    "transit_days",
    "view_of",
    "zone_accuracy",
]
