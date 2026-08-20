"""What serving the orders cost, and what was left.

**Spend is the pinned cost, not a ledger.** Printorian does not keep accounts; it
keeps the breakdown it charged against, and every one of those carries its cost
lines by category (``Breakdown.by_category``). So "куда ушли деньги" is answered
from the same figures the customer was shown, which is the only version of it the
farm can defend against its own invoices. Real supplier spend lands in Phase 6
with purchasing, and this panel gains a second series then rather than changing
meaning.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering.measures import (
    Trend,
    Window,
    as_trend,
    midnight_of,
    share,
    sum_between,
    trend_of,
)
from printorian.contexts.ordering.models import Order
from printorian.contexts.ordering.policies import OrderStatus

#: How many days the revenue sparkline covers. Thirty is the shortest window that
#: shows a weekly rhythm twice, which is what makes a dip readable as a weekend
#: rather than as a problem.
REVENUE_DAYS = 30

#: Cost categories, in the order the spend panel stacks them. Margin and the
#: adjustment lines are deliberately absent: a discount is not money the farm
#: spent, and counting it as such would make every promotion look like a cost
#: overrun.
COST_CATEGORIES: tuple[str, ...] = (
    "material",
    "labor",
    "machine",
    "logistics",
    "overhead",
    "risk",
)


class CategorySpend(BaseModel):
    """One row of "where the money went"."""

    #: A ``pricing.Category`` value. A code, never a label (ADR-0012).
    category: str
    amount: Decimal


class DayRevenue(BaseModel):
    """One point of the sparkline."""

    day: datetime
    amount: Decimal


class FinanceOverview(BaseModel):
    """Received, spent, kept — and the thirty days behind them."""

    received: Trend
    spend: Trend
    profit: Trend
    margin_percent: Decimal
    received_today: Decimal
    spend_today: Decimal
    #: Placed and unpaid: money the farm is owed but has not been given.
    receivable: Decimal
    refund_count: int
    refund_total: Decimal
    spend_by_category: list[CategorySpend] = Field(default_factory=list)
    revenue_by_day: list[DayRevenue] = Field(default_factory=list)


async def finance_overview(db: AsyncSession, window: Window, now: datetime) -> FinanceOverview:
    """The finance row, for one window.

    Everything is keyed on ``paid_at``: an order placed in July and paid in August
    is August's revenue, because that is the month the money arrived. Revenue is
    net of the lateness credit — what the farm may actually bank, not what it
    quoted before it ran late.
    """
    banked = func.sum(Order.total - Order.sla_credit)
    received = await trend_of(db, window, banked, Order.paid_at)
    spent = await _spend_between(db, window.start, window.end)
    spent_before = await _spend_between(db, window.previous_start, window.start)
    spend = as_trend(spent.total, spent_before.total)
    profit = as_trend(received.value - spent.total, received.previous - spent_before.total)

    midnight = midnight_of(now)
    refund_count, refund_total = await _refunds(db, window)
    return FinanceOverview(
        received=received,
        spend=spend,
        profit=profit,
        margin_percent=share(profit.value, received.value),
        received_today=await sum_between(db, banked, Order.paid_at, midnight, now),
        spend_today=(await _spend_between(db, midnight, now)).total,
        receivable=await _receivable(db),
        refund_count=refund_count,
        refund_total=refund_total,
        spend_by_category=[
            CategorySpend(category=name, amount=spent.by_category.get(name, Decimal(0)))
            for name in COST_CATEGORIES
        ],
        revenue_by_day=await _revenue_by_day(db, now),
    )


class _Spend(BaseModel):
    """Cost read out of the pinned breakdowns, whole and by category."""

    total: Decimal = Decimal(0)
    by_category: dict[str, Decimal] = Field(default_factory=dict)


async def _spend_between(db: AsyncSession, start: datetime, end: datetime) -> _Spend:
    """Sum the cost categories of every breakdown paid in the window.

    Summed in Python rather than with ``jsonb_each``. The breakdown is one JSON
    document per order and its ``by_category`` map is a handful of keys, so the
    SQL version buys nothing and costs the ability to read the query — and the
    row count is the orders in a quarter, which is a screenful of work, not a scan.
    """
    rows = await db.scalars(
        select(Order.price_breakdown).where(
            Order.paid_at.is_not(None), Order.paid_at >= start, Order.paid_at < end
        )
    )
    spend = _Spend()
    for breakdown in rows:
        for name, amount in _cost_lines(breakdown):
            spend.by_category[name] = spend.by_category.get(name, Decimal(0)) + amount
            spend.total += amount
    return spend


def _cost_lines(breakdown: Any) -> list[tuple[str, Decimal]]:
    """The cost half of one stored breakdown.

    Deliberately forgiving about shape. A breakdown pinned by an older engine
    version may not carry ``by_category`` at all, and a dashboard that raised on
    one such order would go blank for the whole farm rather than under-report by
    one order.
    """
    if not isinstance(breakdown, dict):
        return []
    by_category = breakdown.get("by_category")
    if not isinstance(by_category, dict):
        return []
    found: list[tuple[str, Decimal]] = []
    for name in COST_CATEGORIES:
        raw = by_category.get(name)
        if raw is None:
            continue
        try:
            found.append((name, Decimal(str(raw))))
        except (ArithmeticError, ValueError):
            continue
    return found


async def _receivable(db: AsyncSession) -> Decimal:
    """Placed, agreed and not yet paid."""
    value = await db.scalar(
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.status == OrderStatus.AWAITING_PAYMENT
        )
    )
    return Decimal(str(value or 0))


async def _refunds(db: AsyncSession, window: Window) -> tuple[int, Decimal]:
    """Refunds settled in the window, by the moment the order last changed."""
    row = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0)).where(
                Order.status == OrderStatus.REFUNDED,
                Order.updated_at >= window.start,
                Order.updated_at < window.end,
            )
        )
    ).one()
    return int(row[0] or 0), Decimal(str(row[1] or 0))


async def _revenue_by_day(db: AsyncSession, now: datetime) -> list[DayRevenue]:
    """Thirty consecutive days, including the ones with no revenue.

    Quiet days are emitted as zeroes rather than omitted: a sparkline drawn from
    present days only compresses a dead week into a short gap and reads as steady
    trade.
    """
    first = midnight_of(now - timedelta(days=REVENUE_DAYS - 1))
    day = func.date(Order.paid_at)
    rows = await db.execute(
        select(day, func.sum(Order.total - Order.sla_credit))
        .where(Order.paid_at.is_not(None), Order.paid_at >= first)
        .group_by(day)
    )
    banked = {str(key): Decimal(str(amount or 0)) for key, amount in rows.all()}
    return [
        DayRevenue(
            day=first + timedelta(days=offset),
            amount=banked.get((first + timedelta(days=offset)).date().isoformat(), Decimal(0)),
        )
        for offset in range(REVENUE_DAYS)
    ]


__all__ = [
    "COST_CATEGORIES",
    "REVENUE_DAYS",
    "CategorySpend",
    "DayRevenue",
    "FinanceOverview",
    "finance_overview",
]
