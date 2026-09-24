"""The parcel after the post, over HTTP: the board, one shipment, and its events.

**Two permissions**, the packaging desk's own. Reading is `VIEW_PRODUCTION`;
writing an event — a scan the carrier reported, a delivery, a return — is
`PACK_ORDER`, the permission that already covers handing the parcel over. No new
`Permission` member: the person who scans the label is the person who records
what the carrier said about it.

**Counts, days and shares leave here; rubles do not.** The kit's carrier table
draws «Средняя цена» and the detail draws «Что и почём». Both are money behind
a production gate if served here, and belong on a `VIEW_FINANCIALS` route of
their own (`contexts/logistics/schemas.py` opens with the rule).

The board and the scorecards need order numbers for their cards, and the numbers
live in the ordering context. Composed here, at the edge, with one lookup per
read (`numbers_for`) rather than a join inside logistics — the same seam
`packaging.py` cuts for the same reason.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from printorian.api.deps import AppClock, CurrentActor, DbSession, requires
from printorian.contexts.identity import Permission
from printorian.contexts.logistics import (
    CarrierScore,
    LogisticsBoard,
    LogisticsService,
    RecordEvent,
    SetTracking,
    ShipmentView,
    ZoneAccuracy,
    carrier_scores,
    damaged_counts,
    shipments_since,
    zone_accuracy,
)
from printorian.contexts.ordering import numbers_for
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/logistics",
    tags=["logistics"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

_PACK = Depends(requires(Permission.PACK_ORDER))

#: The scorecards' window, the kit's own «90 СУТОК»; the closed lane's, one day.
_SCORE_DAYS = 90
_CLOSED_WINDOW = timedelta(hours=24)


def get_logistics(db: DbSession, clock: AppClock) -> LogisticsService:
    return LogisticsService(db, clock)


#: Declared here rather than in `api/deps.py`, which sits at the 400-line gate.
#: `packaging.py` imports it to open the shipment when a parcel ships.
Logistics = Annotated[LogisticsService, Depends(get_logistics)]


class Scorecards(BaseModel):
    """«Перевозчики» and «Сроки доставки», over one window."""

    since: datetime
    until: datetime
    carriers: list[CarrierScore] = Field(default_factory=list)
    zones: list[ZoneAccuracy] = Field(default_factory=list)


async def _named(logistics: LogisticsService, db: DbSession, shipment_id: EntityId) -> str:
    row = await logistics.load(shipment_id)
    return (await numbers_for(db, [row.order_id])).get(row.order_id, "")


@router.get("/board")
async def board(
    logistics: Logistics,
    db: DbSession,
    clock: AppClock,
    closed_since: Annotated[
        datetime | None,
        Query(description="Closed-lane cut-off, tz-aware. Defaults to a day ago."),
    ] = None,
) -> LogisticsBoard:
    """Out, in trouble, and lately arrived — read against one instant."""
    since = closed_since or clock.now() - _CLOSED_WINDOW
    rows = await logistics.board_rows(closed_since=since)
    numbers = await numbers_for(db, [row.order_id for row in rows])
    return logistics.board_of(rows, numbers=numbers, closed_since=since)


@router.get("/scorecards")
async def scorecards(
    db: DbSession,
    clock: AppClock,
    days: Annotated[int, Query(ge=1, le=3660)] = _SCORE_DAYS,
) -> Scorecards:
    """Carriers and zones, computed from the parcels that shipped in the window.

    Nothing here is typed in: «В срок» is `policies.on_time` over delivered parcels
    that carried a promise, «Повреждений» is recorded `damaged` events, and a
    figure with no denominator is null rather than a percentage of nothing.
    """
    until = clock.now()
    since = until - timedelta(days=days)
    rows = await shipments_since(db, since=since)
    return Scorecards(
        since=since,
        until=until,
        carriers=carrier_scores(rows, await damaged_counts(db, since=since)),
        zones=zone_accuracy(rows),
    )


@router.get("/shipments/{shipment_id}")
async def get_shipment(shipment_id: EntityId, logistics: Logistics, db: DbSession) -> ShipmentView:
    """One parcel in full. An unknown id is a 404, not an empty parcel."""
    return await logistics.get(shipment_id, order_number=await _named(logistics, db, shipment_id))


@router.post(
    "/shipments/{shipment_id}/events", status_code=status.HTTP_201_CREATED, dependencies=[_PACK]
)
async def record_event(
    shipment_id: EntityId,
    data: RecordEvent,
    logistics: Logistics,
    db: DbSession,
    actor: CurrentActor,
) -> ShipmentView:
    """What happened to the parcel. A delivered or returned one takes no more."""
    number = await _named(logistics, db, shipment_id)
    return await logistics.record(shipment_id, data, by=actor.user_id, order_number=number)


@router.post("/shipments/{shipment_id}/tracking", dependencies=[_PACK])
async def set_tracking(
    shipment_id: EntityId, data: SetTracking, logistics: Logistics, db: DbSession
) -> ShipmentView:
    number = await _named(logistics, db, shipment_id)
    return await logistics.set_tracking(shipment_id, data, order_number=number)
