"""What a shipment may do, and what counts as delivered on time.

Pure: no session, no clock, no I/O. The stages are the kit's «Путь отправления»
from the moment the parcel leaves the packing post — the three before it
(control passed, packed, labelled) belong to the packaging context and are read
from the pack task, not duplicated here.

**On time is measured against the promise pinned when the parcel shipped**, not
against the zone table as it stands today. A farm that raises a zone's transit
days after a run of late deliveries must not thereby make those deliveries
retroactively punctual (ADR-0020's reasoning, applied to a promise rather than a
price). `Shipment.promised_days` is that pin.

**A shipment with no promise has no punctuality**, only a transit time. The
denominator of «В срок» is the delivered shipments that *carried* a promise, for
the reason the supplier scorecard gives about undated deliveries: putting the
unpromised ones under the line marks a carrier down for every postcode the farm
never drew a zone for.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from printorian.core.errors import ValidationError

SECONDS_PER_DAY = Decimal(86400)
_DAY_PLACES = Decimal("0.1")


class ShipmentStatus(StrEnum):
    """Where a parcel is once it has left the post."""

    #: The carrier has it. «Передан курьеру».
    HANDED_OVER = "handed_over"
    #: A scan somewhere between the post and the door. «В пути».
    IN_TRANSIT = "in_transit"
    #: Something the carrier or a person flagged: no movement, damage, a
    #: refusal at the door. The kit's «Проблемы» lane. Not terminal — a found
    #: parcel goes back to transit or on to delivered.
    PROBLEM = "problem"
    #: «Вручено». Terminal, and the only stage that closes the accuracy record.
    DELIVERED = "delivered"
    #: Came back. Terminal; counts as a shipment and never as on time.
    RETURNED = "returned"

    @property
    def is_terminal(self) -> bool:
        return self in {ShipmentStatus.DELIVERED, ShipmentStatus.RETURNED}


class EventKind(StrEnum):
    """What happened to a parcel. The closed set behind «История трека»."""

    HANDED_OVER = "handed_over"
    SCAN = "scan"
    DELAY = "delay"
    DAMAGED = "damaged"
    DELIVERED = "delivered"
    RETURNED = "returned"
    NOTE = "note"


class EventSource(StrEnum):
    """Who said so. The kit draws «СИСТЕМА» / «ДРАЙВЕР» / a person beside each line."""

    SYSTEM = "system"
    PERSON = "person"
    CARRIER = "carrier"


#: What an event kind does to the status. `NOTE` moves nothing; `DAMAGED` and
#: `DELAY` put the parcel in «Проблемы» without ending it; a `SCAN` on a problem
#: parcel is the carrier saying it moved, so it goes back to transit.
STATUS_AFTER: dict[EventKind, ShipmentStatus | None] = {
    EventKind.HANDED_OVER: ShipmentStatus.HANDED_OVER,
    EventKind.SCAN: ShipmentStatus.IN_TRANSIT,
    EventKind.DELAY: ShipmentStatus.PROBLEM,
    EventKind.DAMAGED: ShipmentStatus.PROBLEM,
    EventKind.DELIVERED: ShipmentStatus.DELIVERED,
    EventKind.RETURNED: ShipmentStatus.RETURNED,
    EventKind.NOTE: None,
}


def assert_open(status: ShipmentStatus) -> None:
    """A delivered or returned parcel takes no further events."""
    if status.is_terminal:
        raise ValidationError("error.logistics.shipment_closed", status=status.value)


def transit_days(shipped_at: datetime, delivered_at: datetime) -> Decimal:
    """Door to door, in days to one place. Refuses a delivery before the dispatch."""
    seconds = Decimal(int((delivered_at - shipped_at).total_seconds()))
    if seconds < 0:
        raise ValidationError(
            "error.logistics.delivered_before_shipped",
            shipped_at=shipped_at.isoformat(),
            delivered_at=delivered_at.isoformat(),
        )
    return (seconds / SECONDS_PER_DAY).quantize(_DAY_PLACES)


def on_time(shipped_at: datetime, delivered_at: datetime, promised_days: int | None) -> bool | None:
    """Whether a delivery kept its pinned promise. ``None`` when there was none.

    Whole days, the way the promise was made: a parcel promised in two days and
    handed over at 2.4 days is late, at 2.0 exactly it is not.
    """
    if promised_days is None:
        return None
    return transit_days(shipped_at, delivered_at) <= Decimal(promised_days)


__all__ = [
    "STATUS_AFTER",
    "EventKind",
    "EventSource",
    "ShipmentStatus",
    "assert_open",
    "on_time",
    "transit_days",
]
