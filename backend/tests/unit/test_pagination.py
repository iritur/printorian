"""Paging the orders table.

Every list endpoint used to return the whole table with its relations eagerly
loaded. These tests hold the two properties that matter: a page is bounded, and the
counter chips above it still describe the *table* rather than the page.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import OrderingService
from printorian.contexts.ordering.policies import OrderStatus
from printorian.contexts.ordering.schemas import DraftLine, PlaceOrder
from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
)
from printorian.core.clock import FixedClock
from printorian.core.errors import ValidationError
from printorian.core.events import EventBus
from printorian.core.pagination import MAX_PAGE_SIZE, Cursor, clamp
from printorian.core.units import Duration, Mass
from tests.conftest import ensure_user

RATES = RateSnapshot()


def a_breakdown():
    return price(
        PriceSpec(
            estimate=PrintEstimate(print_time=Duration.from_hours(3), material_mass=Mass(90)),
            material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
        ),
        RATES,
    )


def an_order() -> PlaceOrder:
    return PlaceOrder(
        customer_email="buyer@example.com",
        lines=[
            DraftLine(
                model_name="part.stl",
                material_code="pla-black",
                estimated_minutes=Decimal(180),
                estimated_grams=Decimal(90),
            )
        ],
    )


@pytest.fixture
def service(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> OrderingService:
    return OrderingService(db_session, clock, bus)


async def _place_many(service: OrderingService, clock: FixedClock, count: int) -> list[str]:
    """Place ``count`` orders, each a second apart so the sort key is unambiguous."""
    numbers = []
    for _ in range(count):
        order = await service.place(an_order(), a_breakdown(), RATES)
        numbers.append(order.number)
        clock.advance(timedelta(seconds=1))
    return numbers


# ------------------------------------------------------------ the cursor


def test_a_cursor_round_trips() -> None:
    from printorian.core.ids import new_id

    cursor = Cursor(id=new_id())
    assert Cursor.decode(cursor.encode()) == cursor


def test_a_forged_cursor_is_refused() -> None:
    """Refused, not silently ignored.

    Starting from the top on a bad token would look like a working paginator that
    quietly repeats page one forever.
    """
    with pytest.raises(ValidationError):
        Cursor.decode("not-a-real-cursor")


def test_the_page_size_is_capped() -> None:
    """A client asking for a million rows gets the cap, not a million rows."""
    assert clamp(1_000_000) == MAX_PAGE_SIZE
    assert clamp(10) == 10
    with pytest.raises(ValidationError):
        clamp(0)


# ------------------------------------------------------------- the pages


async def test_a_page_is_bounded(service: OrderingService, clock: FixedClock) -> None:
    await _place_many(service, clock, 7)

    table = await service.table(limit=3)

    assert len(table.rows) == 3
    assert table.next_cursor is not None


async def test_paging_walks_every_order_exactly_once(
    service: OrderingService, clock: FixedClock
) -> None:
    """No row skipped, none seen twice — the property ``OFFSET`` cannot promise."""
    placed = await _place_many(service, clock, 7)

    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = await service.table(limit=3, cursor=cursor)
        seen.extend(row.number for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert sorted(seen) == sorted(placed)
    assert len(seen) == len(set(seen))


async def test_the_last_page_reports_no_cursor(service: OrderingService, clock: FixedClock) -> None:
    """Exactly one page of rows must not offer a next page that is empty."""
    await _place_many(service, clock, 3)

    table = await service.table(limit=3)

    assert len(table.rows) == 3
    assert table.next_cursor is None


async def test_orders_are_newest_first(service: OrderingService, clock: FixedClock) -> None:
    placed = await _place_many(service, clock, 4)

    table = await service.table(limit=10)

    assert [row.number for row in table.rows] == list(reversed(placed))


# ------------------------------------------------------------- the counts


async def test_counts_describe_the_whole_table_not_the_page(
    service: OrderingService, clock: FixedClock
) -> None:
    """The bug a naive paginator introduces.

    Tallying the returned rows would make the chips say "3 draft" on a table
    holding seven, and the scenario's entire reason for those chips is that they
    describe what is in the table.
    """
    await _place_many(service, clock, 7)

    table = await service.table(limit=2)

    draft = next(count for count in table.counts if count.status is OrderStatus.DRAFT)
    assert len(table.rows) == 2
    assert draft.count == 7
    assert table.total == 7


async def test_every_status_still_gets_a_chip(service: OrderingService, clock: FixedClock) -> None:
    """ "cancelled 0" is information; a missing chip is a gap."""
    await _place_many(service, clock, 2)

    table = await service.table()

    assert {count.status for count in table.counts} == set(OrderStatus)


async def test_a_customers_page_and_counts_are_both_scoped(
    service: OrderingService, clock: FixedClock, db_session: AsyncSession
) -> None:
    """The cabinet must not leak another customer's totals through the chips.

    Scoping the rows but not the aggregate is the natural mistake here, and it
    would show a customer how many orders the farm has.
    """
    from printorian.core.ids import new_id

    mine = new_id()
    # `orders.customer_id` is a real foreign key; PostgreSQL enforces it.
    await ensure_user(db_session, mine, email="mine@example.test")
    await service.place(an_order(), a_breakdown(), RATES, customer_id=mine)
    clock.advance(timedelta(seconds=1))
    await _place_many(service, clock, 4)

    table = await service.table(customer_id=mine)

    assert table.total == 1
    assert len(table.rows) == 1
    assert sum(count.count for count in table.counts) == 1
