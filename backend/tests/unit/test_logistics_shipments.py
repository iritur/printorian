"""Shipments: the promise pinned at dispatch, the events after it, and the two scorecards.

Issue #36. What is pinned here is what a screen could get wrong quietly:

* **The promise is the zone's transit days at ship time**, from the order's own
  pinned table, and it stays put when the zone changes afterwards.
* **A postcode no zone claims is a shipment with no promise** — not a promise of
  zero days, and never in the denominator of «В срок».
* **Arrival is an event.** `delivered_at` is set by the `delivered` event alone,
  a delivery dated before the dispatch is refused, and a closed parcel takes no
  further events.
* **The scorecards count what was measured.** «В срок» is over delivered parcels
  that carried a promise; «Точность» groups by the *pinned* promise, so a zone
  whose promise changed mid-window appears twice rather than as one blurred row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.logistics import (
    EventKind,
    EventSource,
    LogisticsService,
    RecordEvent,
    Shipment,
    ShipmentStatus,
    carrier_scores,
    on_time,
    transit_days,
    zone_accuracy,
)
from printorian.contexts.pricing import ShippingZone, ZoneTariffs
from printorian.core.clock import FixedClock
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import EntityId, new_id
from tests.factories import ensure_order
from tests.unit._packaging_support import a_parcel

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
ZONES = ZoneTariffs(
    zones=(
        ShippingZone(code="msk", transit_days=1, postcode_prefixes=("101", "1")),
        ShippingZone(code="cfo", transit_days=3, postcode_prefixes=("3",)),
    )
)


@pytest.fixture
def logistics(db_session: AsyncSession, clock: FixedClock) -> LogisticsService:
    return LogisticsService(db_session, clock)


_numbers = iter(range(1, 10_000))


async def an_order(db: AsyncSession) -> EntityId:
    order_id = new_id()
    await ensure_order(db, order_id, number=f"ORD-L{next(_numbers)}")
    return order_id


async def shipped(
    logistics: LogisticsService,
    db: AsyncSession,
    clock: FixedClock,
    *,
    postcode: str = "101000",
    carrier: str = "courier",
    at: datetime = T0,
    zones: ZoneTariffs | None = ZONES,
) -> EntityId:
    clock.set(at)
    order = await an_order(db)
    view = await logistics.open_shipment(
        order_id=order,
        order_number="ORD-1",
        pack_task_id=None,
        carrier_code=carrier,
        postcode=postcode,
        zones=zones,
    )
    return view.id


async def delivered(
    logistics: LogisticsService, shipment_id: EntityId, clock: FixedClock, *, at: datetime
) -> None:
    clock.set(at)
    await logistics.record(
        shipment_id, RecordEvent(kind=EventKind.DELIVERED), by=None, order_number="ORD-1"
    )


# ------------------------------------------------------------- the rules


def test_transit_days_is_door_to_door_to_one_place() -> None:
    assert transit_days(T0, T0 + timedelta(hours=36)) == Decimal("1.5")
    assert transit_days(T0, T0) == Decimal("0.0")


def test_a_delivery_before_the_dispatch_is_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        transit_days(T0, T0 - timedelta(minutes=1))
    assert raised.value.code == "error.logistics.delivered_before_shipped"


def test_on_time_is_against_the_pinned_promise_and_none_without_one() -> None:
    assert on_time(T0, T0 + timedelta(days=2), 2) is True
    # Tenths of a day, the way `transit_days` measures: an hour over is still
    # 2.0 and kept; six hours over is 2.3 and late.
    assert on_time(T0, T0 + timedelta(days=2, hours=1), 2) is True
    assert on_time(T0, T0 + timedelta(days=2, hours=6), 2) is False
    assert on_time(T0, T0 + timedelta(days=9), None) is None


# ------------------------------------------------------------- opening


async def test_the_zone_and_its_promise_are_pinned_from_the_orders_own_table(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    shipment_id = await shipped(logistics, db_session, clock, postcode="301000")

    view = await logistics.get(shipment_id, order_number="ORD-1")

    assert view.zone_code == "cfo"
    assert view.promised_days == 3
    assert view.status is ShipmentStatus.HANDED_OVER
    assert [event.kind for event in view.events] == [EventKind.HANDED_OVER]
    assert view.events[0].source is EventSource.SYSTEM
    assert view.on_time is None and view.transit_days is None


async def test_a_postcode_no_zone_claims_is_a_shipment_with_no_promise(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    """Not zero days, and not the flat rate's idea of a zone: nothing was promised."""
    shipment_id = await shipped(logistics, db_session, clock, postcode="690000")

    view = await logistics.get(shipment_id, order_number="ORD-1")

    assert view.zone_code is None
    assert view.promised_days is None


async def test_an_order_with_no_pinned_rates_gets_a_shipment_with_no_promise(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    shipment_id = await shipped(logistics, db_session, clock, zones=None)
    view = await logistics.get(shipment_id, order_number="ORD-1")
    assert view.promised_days is None


async def test_opening_twice_for_one_parcel_returns_the_same_shipment(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    """The ship route may be retried; the second call must not mint a second row."""
    clock.set(T0)
    _packaging, parcel = await a_parcel(db_session, clock)
    order = parcel.order_id

    first = await logistics.open_shipment(
        order_id=order,
        order_number="ORD-1",
        pack_task_id=parcel.id,
        carrier_code="cdek",
        postcode="101000",
        zones=ZONES,
    )
    second = await logistics.open_shipment(
        order_id=order,
        order_number="ORD-1",
        pack_task_id=parcel.id,
        carrier_code="cdek",
        postcode="101000",
        zones=ZONES,
    )

    assert first.id == second.id


# ------------------------------------------------------------- events


async def test_the_status_follows_the_event_and_delivered_is_recorded_once(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    shipment_id = await shipped(logistics, db_session, clock)

    clock.set(T0 + timedelta(hours=6))
    scanned = await logistics.record(
        shipment_id,
        RecordEvent(kind=EventKind.SCAN, source=EventSource.CARRIER, note="в сортировочном центре"),
        by=None,
        order_number="ORD-1",
    )
    clock.set(T0 + timedelta(hours=30))
    delayed = await logistics.record(
        shipment_id, RecordEvent(kind=EventKind.DELAY), by=None, order_number="ORD-1"
    )
    clock.set(T0 + timedelta(hours=40))
    moving = await logistics.record(
        shipment_id, RecordEvent(kind=EventKind.SCAN), by=None, order_number="ORD-1"
    )
    await delivered(logistics, shipment_id, clock, at=T0 + timedelta(hours=44))
    view = await logistics.get(shipment_id, order_number="ORD-1")

    assert scanned.status is ShipmentStatus.IN_TRANSIT
    assert delayed.status is ShipmentStatus.PROBLEM
    assert moving.status is ShipmentStatus.IN_TRANSIT
    assert view.status is ShipmentStatus.DELIVERED
    assert view.delivered_at == T0 + timedelta(hours=44)
    assert view.transit_days == Decimal("1.8")
    # Promised one day (msk), delivered in 1.8: late, and said so.
    assert view.on_time is False
    assert [event.kind for event in view.events] == [
        EventKind.HANDED_OVER,
        EventKind.SCAN,
        EventKind.DELAY,
        EventKind.SCAN,
        EventKind.DELIVERED,
    ]


async def test_a_delivery_dated_before_the_dispatch_is_refused_and_writes_nothing(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    shipment_id = await shipped(logistics, db_session, clock)

    with pytest.raises(ValidationError) as raised:
        await logistics.record(
            shipment_id,
            RecordEvent(kind=EventKind.DELIVERED, at=T0 - timedelta(days=1)),
            by=None,
            order_number="ORD-1",
        )

    assert raised.value.code == "error.logistics.delivered_before_shipped"
    view = await logistics.get(shipment_id, order_number="ORD-1")
    assert view.delivered_at is None and view.status is ShipmentStatus.HANDED_OVER
    assert len(view.events) == 1


async def test_a_closed_parcel_takes_no_further_events(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    shipment_id = await shipped(logistics, db_session, clock)
    await delivered(logistics, shipment_id, clock, at=T0 + timedelta(hours=20))

    with pytest.raises(ValidationError) as raised:
        await logistics.record(
            shipment_id, RecordEvent(kind=EventKind.NOTE), by=None, order_number="ORD-1"
        )
    assert raised.value.code == "error.logistics.shipment_closed"


async def test_an_unknown_shipment_is_a_404(logistics: LogisticsService) -> None:
    with pytest.raises(NotFoundError) as raised:
        await logistics.get(new_id(), order_number="")
    assert raised.value.code == "error.logistics.shipment_not_found"


# ------------------------------------------------------------- the board


async def test_the_board_sorts_by_status_and_bounds_the_closed_lane_by_time(
    logistics: LogisticsService, db_session: AsyncSession, clock: FixedClock
) -> None:
    out = await shipped(logistics, db_session, clock, at=T0)
    trouble = await shipped(logistics, db_session, clock, at=T0)
    old = await shipped(logistics, db_session, clock, at=T0)
    fresh = await shipped(logistics, db_session, clock, at=T0)
    clock.set(T0 + timedelta(hours=5))
    await logistics.record(
        trouble, RecordEvent(kind=EventKind.DAMAGED), by=None, order_number="ORD-1"
    )
    await delivered(logistics, old, clock, at=T0 + timedelta(hours=10))
    await delivered(logistics, fresh, clock, at=T0 + timedelta(days=3))
    clock.set(T0 + timedelta(days=3, hours=1))

    rows = await logistics.board_rows(closed_since=T0 + timedelta(days=2))
    board = logistics.board_of(rows, numbers={}, closed_since=T0 + timedelta(days=2))

    assert [view.id for view in board.in_transit] == [out]
    assert [view.id for view in board.problems] == [trouble]
    # Delivered a day after dispatch — outside the closed window — is gone from
    # the board; the one delivered this morning is on it.
    assert [view.id for view in board.closed] == [fresh]
    assert board.in_transit[0].days_out == Decimal("3.0")


# ------------------------------------------------------------- the scorecards


def a_row(
    *,
    carrier: str = "courier",
    zone: str | None = "msk",
    promised: int | None = 1,
    days: Decimal | None,
    status: ShipmentStatus = ShipmentStatus.DELIVERED,
) -> Shipment:
    row = Shipment(
        id=new_id(),
        order_id=new_id(),
        carrier_code=carrier,
        zone_code=zone,
        promised_days=promised,
        status=status if days is not None else ShipmentStatus.IN_TRANSIT,
        shipped_at=T0,
        delivered_at=(T0 + timedelta(days=float(days))) if days is not None else None,
    )
    row.events = []
    return row


def test_carrier_on_time_is_over_delivered_parcels_that_carried_a_promise() -> None:
    """Five parcels: two kept the promise, one missed it, one had no promise, one is
    still out. The share is 2 of 3, and the other two are counted beside it."""
    rows = [
        a_row(days=Decimal("0.8")),
        a_row(days=Decimal("1.0")),
        a_row(days=Decimal("1.4")),
        a_row(zone=None, promised=None, days=Decimal("4.0")),
        a_row(days=None),
    ]

    [score] = carrier_scores(rows, {"courier": 1})

    assert score.shipments == 5
    assert score.delivered == 4
    assert score.promised == 3
    assert score.on_time == 2
    assert score.on_time_share == Decimal("0.6667")
    assert score.damaged == 1
    assert score.mean_transit_days == Decimal("1.8")


def test_a_carrier_with_nothing_delivered_has_a_null_share_not_a_perfect_one() -> None:
    [score] = carrier_scores([a_row(days=None)], {})
    assert score.on_time_share is None
    assert score.mean_transit_days is None


def test_zone_accuracy_groups_by_the_pinned_promise() -> None:
    """The msk promise was raised from one day to two mid-window: two rows, each
    measured against what was promised at the time, never one blurred average."""
    rows = [
        a_row(promised=1, days=Decimal("0.9")),
        a_row(promised=1, days=Decimal("1.5")),
        a_row(promised=2, days=Decimal("1.5")),
        a_row(zone="cfo", promised=3, days=Decimal("2.6")),
        a_row(zone=None, promised=None, days=Decimal("3.0")),
        a_row(promised=1, days=None),
    ]

    accuracy = zone_accuracy(rows)

    assert [(row.zone_code, row.promised_days, row.delivered, row.on_time) for row in accuracy] == [
        ("cfo", 3, 1, 1),
        ("msk", 1, 2, 1),
        ("msk", 2, 1, 1),
    ]
    assert accuracy[1].accuracy == Decimal("0.5000")
    assert accuracy[1].mean_transit_days == Decimal("1.2")
