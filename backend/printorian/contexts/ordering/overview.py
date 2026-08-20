"""What the farm sold: the dashboard's orders row and its stage funnel.

Kept out of ``service.py`` for the same reason ``production/reads.py`` is: these
are bounded aggregate queries with no clock, bus or transaction of their own, and
the service is already at the length gate. The window and the trend arithmetic
they share with the finance row live in `measures.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering.measures import Trend, Window, month_window, trend_of
from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.ordering.policies import OrderStatus
from printorian.core.ids import EntityId

#: Statuses that mean work is on the floor right now. `PREP` and `PRICE_REVIEW`
#: are included — someone is holding them — while `DRAFT` and `AWAITING_PAYMENT`
#: are not, because nothing has been committed to them yet.
IN_PROGRESS: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.PAID,
        OrderStatus.PREP,
        OrderStatus.PRICE_REVIEW,
        OrderStatus.QUEUED,
        OrderStatus.PRINTING,
        OrderStatus.POST_PRODUCTION,
        OrderStatus.QUALITY_CHECK,
        OrderStatus.PACKING,
    }
)


class StatusSlice(BaseModel):
    """One bar of the stage funnel."""

    status: OrderStatus
    count: int


class OrdersOverview(BaseModel):
    """The scenario's orders row: what came in, what is moving, what it is worth."""

    placed: Trend
    #: Orders this calendar month, against the whole of last month.
    #:
    #: Separate from `placed`, which follows the period switch: the kit's orders
    #: row carries both, because "14 today" and "248 this month" answer different
    #: questions and a farm reads them together.
    placed_month: Trend
    paid: int
    awaiting_payment: int
    in_progress: int
    #: Every non-terminal status with a count. The longest bar is the bottleneck,
    #: which is a length comparison rather than a reading task.
    funnel: list[StatusSlice] = Field(default_factory=list)
    average_order: Trend
    median_order: Decimal
    lines_per_order: Decimal


async def orders_overview(db: AsyncSession, window: Window) -> OrdersOverview:
    """The orders row, for one window."""
    placed = await trend_of(db, window, func.count(Order.id), Order.created_at)
    placed_month = await trend_of(
        db, month_window(window.end), func.count(Order.id), Order.created_at
    )
    average = await trend_of(db, window, func.avg(Order.total), Order.created_at)
    tally = await _tally(db)

    return OrdersOverview(
        placed=placed,
        placed_month=placed_month,
        paid=await _count_created_in(db, window, OrderStatus.PAID),
        awaiting_payment=await _count_created_in(db, window, OrderStatus.AWAITING_PAYMENT),
        # The funnel counts the farm as it stands, not as it traded this window: an
        # order placed last month and still in post-production is exactly the kind
        # of stuck work the panel exists to surface.
        in_progress=sum(count for status, count in tally.items() if status in IN_PROGRESS),
        funnel=[
            StatusSlice(status=status, count=tally.get(status, 0))
            for status in OrderStatus
            if not status.is_terminal and status is not OrderStatus.DRAFT
        ],
        average_order=average,
        median_order=await _median_order_total(db, window),
        lines_per_order=await _lines_per_order(db, window),
    )


async def numbers_for(db: AsyncSession, ids: Sequence[EntityId]) -> dict[EntityId, str]:
    """What these orders are called.

    A label lookup, kept deliberately narrow. The schedule strip needs it because
    a bar reading `01a01a12` is unreadable on a screen an operator uses to find
    work, and production — which owns the jobs — is not allowed to know what an
    order is named.
    """
    if not ids:
        return {}
    rows = await db.execute(select(Order.id, Order.number).where(Order.id.in_(set(ids))))
    return dict(rows.all())  # type: ignore[arg-type]


async def _tally(db: AsyncSession) -> dict[OrderStatus, int]:
    """How many orders sit in each status right now, across the whole farm."""
    rows = await db.execute(select(Order.status, func.count(Order.id)).group_by(Order.status))
    return {status: count for status, count in rows.all()}  # noqa: C416 — typed unpack


async def _count_created_in(db: AsyncSession, window: Window, status: OrderStatus) -> int:
    value = await db.scalar(
        select(func.count(Order.id)).where(
            Order.status == status,
            Order.created_at >= window.start,
            Order.created_at < window.end,
        )
    )
    return int(value or 0)


async def _median_order_total(db: AsyncSession, window: Window) -> Decimal:
    """The middle order, which the average hides.

    One large order moves the mean and not the median, and the gap between the two
    is the fact worth reading: a mean far above the median means the farm's revenue
    is resting on a handful of customers.
    """
    totals = list(
        await db.scalars(
            select(Order.total)
            .where(Order.created_at >= window.start, Order.created_at < window.end)
            .order_by(Order.total)
        )
    )
    if not totals:
        return Decimal(0)
    middle = len(totals) // 2
    if len(totals) % 2 == 1:
        return Decimal(totals[middle])
    return (Decimal(totals[middle - 1]) + Decimal(totals[middle])) / 2


async def _lines_per_order(db: AsyncSession, window: Window) -> Decimal:
    """Items per order, counting quantity rather than rows.

    Two of a bracket is two things printed, and the figure sits beside the average
    order value where it explains it — a rising cheque with flat items per order is
    a price change, the same rise with more items is a bigger basket.
    """
    orders = await db.scalar(
        select(func.count(Order.id)).where(
            Order.created_at >= window.start, Order.created_at < window.end
        )
    )
    if not orders:
        return Decimal(0)
    lines = await db.scalar(
        select(func.coalesce(func.sum(OrderLine.quantity), 0))
        .select_from(OrderLine)
        .join(Order, Order.id == OrderLine.order_id)
        .where(Order.created_at >= window.start, Order.created_at < window.end)
    )
    return (Decimal(str(lines or 0)) / Decimal(orders)).quantize(Decimal("0.1"))


__all__ = ["IN_PROGRESS", "OrdersOverview", "StatusSlice", "numbers_for", "orders_overview"]
