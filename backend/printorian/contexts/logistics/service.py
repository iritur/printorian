"""Shipments as the farm opens, tracks and closes them.

Issue #36. The parcel's life before the carrier — control, packing, label — is
the packaging context's; this starts where `PackagingService.ship` ends. The API
layer composes the two (`api/routers/packaging.py`): after a parcel ships, it
opens the shipment here with the order's delivery postcode and the order's own
*pinned* zone table, so the promise recorded is the one the customer was quoted
against, not whatever the settings screen says today (ADR-0020, applied to a
promise).

Every change of state is an event, and the status is what the last event says
(`policies.STATUS_AFTER`). `delivered_at` and `returned_at` are set by the
events that end a parcel and by nothing else, so the accuracy table reads facts
somebody stated rather than a column a bulk update could set.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.logistics.models import Shipment, ShipmentEvent
from printorian.contexts.logistics.policies import (
    SECONDS_PER_DAY,
    STATUS_AFTER,
    EventKind,
    EventSource,
    ShipmentStatus,
    assert_open,
    on_time,
    transit_days,
)
from printorian.contexts.logistics.schemas import (
    LogisticsBoard,
    RecordEvent,
    SetTracking,
    ShipmentEventView,
    ShipmentView,
)
from printorian.contexts.pricing import ZoneTariffs, zone_for
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError
from printorian.core.ids import EntityId

_DAY_PLACES = Decimal("0.1")


def view_of(shipment: Shipment, *, order_number: str, now: datetime) -> ShipmentView:
    """The shipment as the board reads it, measured against ``now``."""
    delivered = shipment.delivered_at
    days = (
        transit_days(shipment.shipped_at, delivered)
        if delivered is not None
        else (Decimal(int((now - shipment.shipped_at).total_seconds())) / SECONDS_PER_DAY).quantize(
            _DAY_PLACES
        )
    )
    return ShipmentView(
        id=shipment.id,
        order_id=shipment.order_id,
        order_number=order_number,
        pack_task_id=shipment.pack_task_id,
        carrier_code=shipment.carrier_code,
        zone_code=shipment.zone_code,
        promised_days=shipment.promised_days,
        tracking_number=shipment.tracking_number,
        status=shipment.status,
        shipped_at=shipment.shipped_at,
        delivered_at=delivered,
        returned_at=shipment.returned_at,
        transit_days=days if delivered is not None else None,
        on_time=(
            on_time(shipment.shipped_at, delivered, shipment.promised_days)
            if delivered is not None
            else None
        ),
        days_out=max(days, Decimal(0)),
        events=[ShipmentEventView.model_validate(event) for event in shipment.events],
    )


class LogisticsService:
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._db = session
        self._clock = clock

    async def open_shipment(
        self,
        *,
        order_id: EntityId,
        order_number: str,
        pack_task_id: EntityId | None,
        carrier_code: str,
        postcode: str,
        zones: ZoneTariffs | None,
        shipped_at: datetime | None = None,
    ) -> ShipmentView:
        """A parcel has left the post. Once per parcel.

        ``zones`` is the order's pinned table, or ``None`` for an order that
        predates rate snapshots — then the parcel gets no zone and no promise,
        which is the honest record of "the farm never told this customer a
        transit time". A second call for the same parcel returns the shipment
        already opened rather than a duplicate: the ship route may be retried.
        """
        now = shipped_at or self._clock.now()
        if pack_task_id is not None:
            existing = await self._db.scalar(
                select(Shipment).where(Shipment.pack_task_id == pack_task_id)
            )
            if existing is not None:
                return view_of(existing, order_number=order_number, now=self._clock.now())
        zone = zone_for(zones, postcode) if zones is not None else None
        shipment = Shipment(
            order_id=order_id,
            pack_task_id=pack_task_id,
            carrier_code=carrier_code,
            zone_code=zone.code if zone is not None else None,
            promised_days=zone.transit_days if zone is not None else None,
            tracking_number=None,
            status=ShipmentStatus.HANDED_OVER,
            shipped_at=now,
        )
        shipment.events = [
            ShipmentEvent(at=now, kind=EventKind.HANDED_OVER, source=EventSource.SYSTEM)
        ]
        self._db.add(shipment)
        await self._db.flush()
        return view_of(shipment, order_number=order_number, now=self._clock.now())

    async def record(
        self,
        shipment_id: EntityId,
        data: RecordEvent,
        *,
        by: EntityId | None,
        order_number: str,
    ) -> ShipmentView:
        """What happened next. The status follows the event; a closed parcel takes none."""
        shipment = await self._load(shipment_id)
        assert_open(shipment.status)
        at = data.at or self._clock.now()
        if data.kind is EventKind.DELIVERED:
            # Refused before anything is written, so a typo in the date does not
            # leave a parcel delivered-with-no-time.
            transit_days(shipment.shipped_at, at)
            shipment.delivered_at = at
        if data.kind is EventKind.RETURNED:
            shipment.returned_at = at
        shipment.events.append(
            ShipmentEvent(at=at, kind=data.kind, source=data.source, note=data.note, recorded_by=by)
        )
        after = STATUS_AFTER[data.kind]
        if after is not None:
            shipment.status = after
        await self._db.flush()
        return view_of(shipment, order_number=order_number, now=self._clock.now())

    async def set_tracking(
        self, shipment_id: EntityId, data: SetTracking, *, order_number: str
    ) -> ShipmentView:
        shipment = await self._load(shipment_id)
        shipment.tracking_number = data.tracking_number
        await self._db.flush()
        return view_of(shipment, order_number=order_number, now=self._clock.now())

    async def get(self, shipment_id: EntityId, *, order_number: str) -> ShipmentView:
        return view_of(
            await self._load(shipment_id), order_number=order_number, now=self._clock.now()
        )

    async def load(self, shipment_id: EntityId) -> Shipment:
        """The row, for a caller that needs its `order_id` before it can name it."""
        return await self._load(shipment_id)

    async def board_rows(self, *, closed_since: datetime) -> list[Shipment]:
        """Everything out, plus what arrived or came back since ``closed_since``.

        Rows rather than views: the caller owns the order numbers (a lookup across
        the ordering context that belongs at the edge) and builds the board with
        `board_of`.
        """
        rows = await self._db.scalars(
            select(Shipment)
            .where(
                (~Shipment.status.in_([ShipmentStatus.DELIVERED, ShipmentStatus.RETURNED]))
                | (Shipment.delivered_at >= closed_since)
                | (Shipment.returned_at >= closed_since)
            )
            .order_by(Shipment.shipped_at)
        )
        return list(rows.unique().all())

    def board_of(
        self, rows: list[Shipment], *, numbers: dict[EntityId, str], closed_since: datetime
    ) -> LogisticsBoard:
        now = self._clock.now()
        board = LogisticsBoard(at=now, closed_since=closed_since)
        for shipment in rows:
            view = view_of(shipment, order_number=numbers.get(shipment.order_id, ""), now=now)
            if shipment.status is ShipmentStatus.PROBLEM:
                board.problems.append(view)
            elif shipment.status.is_terminal:
                board.closed.append(view)
            else:
                board.in_transit.append(view)
        board.closed.sort(
            key=lambda view: view.delivered_at or view.returned_at or now, reverse=True
        )
        return board

    async def _load(self, shipment_id: EntityId) -> Shipment:
        shipment = await self._db.get(Shipment, shipment_id)
        if shipment is None:
            raise NotFoundError("error.logistics.shipment_not_found", shipment_id=str(shipment_id))
        return shipment


__all__ = ["LogisticsService", "view_of"]
