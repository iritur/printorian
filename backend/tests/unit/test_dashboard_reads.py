"""The dashboard's commercial reads: what the farm sold, and what it cost.

Three claims are pinned here, because each is a way a summary screen lies quietly:

* the comparison window is the *same length* as the window it compares against;
* spend counts costs and not discounts, so a promotion cannot look like an
  overrun;
* one unreadable order must not take the whole farm's finance panel with it.

The floor's half of the dashboard — filament, the schedule strip, throughput —
is in `test_dashboard_floor_reads.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import Period, finance_overview, orders_overview, window_for
from printorian.contexts.ordering.policies import OrderStatus
from tests.unit._dashboard_support import NOW, an_order

# ------------------------------------------------------------------- windows


def test_the_comparison_window_is_the_same_length_as_the_window() -> None:
    """Month-to-date against the whole previous month is the classic KPI lie."""
    window = window_for(Period.MONTH, NOW)

    assert window.start == datetime(2026, 8, 1, tzinfo=UTC)
    assert window.end == NOW
    assert window.end - window.start == window.start - window.previous_start


def test_a_quarter_starts_at_its_own_first_month() -> None:
    window = window_for(Period.QUARTER, NOW)

    assert window.start == datetime(2026, 7, 1, tzinfo=UTC)


def test_today_starts_at_midnight_not_an_hour_ago() -> None:
    window = window_for(Period.TODAY, NOW)

    assert window.start == datetime(2026, 8, 19, tzinfo=UTC)


# -------------------------------------------------------------------- orders


async def test_the_funnel_counts_the_farm_as_it_stands(db_session: AsyncSession) -> None:
    """An order stuck since last month is exactly what the funnel is for.

    So the funnel is a tally of the whole farm, while `placed` is a tally of the
    window — two different questions that a single "orders" panel would conflate.
    """
    await an_order(db_session, status=OrderStatus.PRINTING, created_at=NOW - timedelta(days=40))
    await an_order(db_session, status=OrderStatus.PACKING, created_at=NOW - timedelta(hours=2))
    await db_session.flush()

    overview = await orders_overview(db_session, window_for(Period.TODAY, NOW))

    stages = {slice_.status: slice_.count for slice_ in overview.funnel}
    assert stages[OrderStatus.PRINTING] == 1
    assert stages[OrderStatus.PACKING] == 1
    assert overview.in_progress == 2
    # Only one of them was placed today.
    assert overview.placed.value == 1


async def test_a_first_period_reports_no_change_rather_than_zero(
    db_session: AsyncSession,
) -> None:
    await an_order(db_session, status=OrderStatus.PAID, created_at=NOW - timedelta(hours=1))
    await db_session.flush()

    overview = await orders_overview(db_session, window_for(Period.TODAY, NOW))

    assert overview.placed.value == 1
    assert overview.placed.previous == 0
    assert overview.placed.change_percent is None


async def test_the_median_is_reported_beside_the_mean(db_session: AsyncSession) -> None:
    """One large order moves the mean and not the median. Both are shown."""
    for total in ("1000", "1000", "1000", "97000"):
        await an_order(
            db_session,
            status=OrderStatus.PAID,
            created_at=NOW - timedelta(hours=1),
            total=Decimal(total),
        )
    await db_session.flush()

    overview = await orders_overview(db_session, window_for(Period.TODAY, NOW))

    assert overview.median_order == Decimal(1000)
    assert overview.average_order.value == Decimal(25000)


# ------------------------------------------------------------------- finance


async def test_spend_counts_costs_and_not_discounts(db_session: AsyncSession) -> None:
    """A discount is not money the farm spent.

    Counting the adjustment lines would make every promotion read as a cost
    overrun, which is how a spend panel teaches people to stop running promotions.
    """
    await an_order(
        db_session,
        status=OrderStatus.COMPLETED,
        created_at=NOW - timedelta(hours=3),
        paid_at=NOW - timedelta(hours=2),
        total=Decimal(1000),
        breakdown={
            "by_category": {
                "material": "300",
                "labor": "200",
                "adjustment": "-150",
                "margin": "500",
            }
        },
    )
    await db_session.flush()

    finance = await finance_overview(db_session, window_for(Period.TODAY, NOW), NOW)

    assert finance.spend.value == Decimal(500)
    assert finance.profit.value == Decimal(500)
    assert {row.category: row.amount for row in finance.spend_by_category}["material"] == Decimal(
        300
    )


async def test_revenue_is_net_of_the_lateness_credit(db_session: AsyncSession) -> None:
    """What the farm may bank, not what it quoted before it ran late."""
    await an_order(
        db_session,
        status=OrderStatus.SHIPPED,
        created_at=NOW - timedelta(hours=3),
        paid_at=NOW - timedelta(hours=2),
        total=Decimal(1000),
        sla_credit=Decimal(120),
    )
    await db_session.flush()

    finance = await finance_overview(db_session, window_for(Period.TODAY, NOW), NOW)

    assert finance.received.value == Decimal(880)


async def test_a_breakdown_without_categories_does_not_blank_the_panel(
    db_session: AsyncSession,
) -> None:
    """One unreadable order must not take the whole farm's finance panel with it."""
    await an_order(
        db_session,
        status=OrderStatus.COMPLETED,
        created_at=NOW - timedelta(hours=3),
        paid_at=NOW - timedelta(hours=2),
        total=Decimal(400),
        breakdown={"lines": []},
    )
    await an_order(
        db_session,
        status=OrderStatus.COMPLETED,
        created_at=NOW - timedelta(hours=3),
        paid_at=NOW - timedelta(hours=1),
        total=Decimal(600),
        breakdown={"by_category": {"material": "100"}},
    )
    await db_session.flush()

    finance = await finance_overview(db_session, window_for(Period.TODAY, NOW), NOW)

    assert finance.received.value == Decimal(1000)
    assert finance.spend.value == Decimal(100)


async def test_the_sparkline_carries_a_point_for_every_day(db_session: AsyncSession) -> None:
    """Omitting quiet days would compress a dead week into a short gap."""
    finance = await finance_overview(db_session, window_for(Period.TODAY, NOW), NOW)

    assert len(finance.revenue_by_day) == 30
    assert finance.revenue_by_day[-1].day.date() == NOW.date()
