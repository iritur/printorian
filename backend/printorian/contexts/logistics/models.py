"""The shipment record: one row per parcel that left the post, and every event since.

Two tables, and the shape is chosen so that the two figures the logistics screen
is built on — «В срок» per carrier and «Точность» per zone — are computed from
rows nobody can overwrite.

**The promise is pinned on the row.** `promised_days` is the zone's transit time
*as it stood when the parcel shipped*. A zone edited afterwards changes the next
promise and never this one, so a carrier cannot be made punctual by moving the
goalposts (the reason `policies.py` gives). `zone_code` is pinned beside it for
the same reason: a postcode re-drawn into a different zone tomorrow does not
move yesterday's parcel.

**Arrival is a recorded observation, not a status flip.** `delivered_at` is set
by the `DELIVERED` event and by nothing else, so the accuracy table reads a fact
somebody stated rather than a column a bulk update could set.

`order_id` is ``RESTRICT`` for the reason `printer_failures.printer_id` is: an
order delete that erased the record of how its parcel travelled would erase the
evidence the carrier scorecard rests on. `pack_task_id` is ``SET NULL``: the
parcel is the shipment's origin, not its identity, and a packing row cleaned up
later must not take the delivery record with it.

There is no retention on either table, deliberately: an accuracy figure whose
history expires is one that quietly improves.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.logistics.policies import EventKind, EventSource, ShipmentStatus
from printorian.core.db import Entity, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class Shipment(Entity):
    """One parcel from the moment the carrier took it."""

    __tablename__ = "shipments"
    __table_args__ = (
        # The board reads everything not terminal; the scorecards read a window
        # of `shipped_at`. Both lead with the column they filter on.
        Index("ix_shipments_status_shipped", "status", "shipped_at"),
        Index("ix_shipments_shipped_at", "shipped_at"),
        Index("ix_shipments_carrier_shipped", "carrier_code", "shipped_at"),
        Index("ix_shipments_order_id", "order_id"),
        Index("ix_shipments_pack_task_id", "pack_task_id"),
        CheckConstraint(
            "delivered_at IS NULL OR delivered_at >= shipped_at", name="delivered_after_shipped"
        ),
        CheckConstraint("promised_days IS NULL OR promised_days >= 0", name="promise_not_negative"),
    )

    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    pack_task_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("packaging_tasks.id", ondelete="SET NULL"), nullable=True
    )
    #: The carrier's code, copied from the parcel. A code, rendered by the client.
    carrier_code: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    #: The zone the delivery postcode fell in when the parcel shipped, from the
    #: order's own pinned rate snapshot. ``NULL`` is "no zone claimed this
    #: postcode" — a delivery with a transit time and no promise.
    zone_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: The zone's transit days at ship time. ``NULL`` with `zone_code`, and read
    #: nowhere else: `policies.on_time` compares against this and never against
    #: the live table.
    promised_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[ShipmentStatus] = mapped_column(enum_column(ShipmentStatus), nullable=False)
    shipped_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    #: Set by the `DELIVERED` event only. ``NULL`` while the parcel is out.
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    events: Mapped[list[ShipmentEvent]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentEvent.at",
        lazy="selectin",
    )


class ShipmentEvent(Entity):
    """One line of «История трека». Insert-only; the history is the list of these."""

    __tablename__ = "shipment_events"
    __table_args__ = (
        Index("ix_shipment_events_shipment_at", "shipment_id", "at"),
        Index("ix_shipment_events_recorded_by", "recorded_by"),
    )

    shipment_id: Mapped[EntityId] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    #: When it happened — the carrier's scan time when one is known, else the
    #: moment it was recorded. Never defaulted to the sweep's clock by a caller
    #: that had a better answer.
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    kind: Mapped[EventKind] = mapped_column(enum_column(EventKind), nullable=False)
    source: Mapped[EventSource] = mapped_column(enum_column(EventSource), nullable=False)
    #: Prose in the shop's own words, or the carrier's status text verbatim.
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recorded_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    shipment: Mapped[Shipment] = relationship(back_populates="events")


__all__ = ["Shipment", "ShipmentEvent"]
