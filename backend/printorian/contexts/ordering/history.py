"""What one customer's order history adds up to.

A module of its own rather than four more methods on `OrderingService`: these are
read-only aggregations over the same table, they share a set of counting rules,
and stating those rules once at the top is worth more than the proximity.

**The counting rules, which every figure here obeys.**

* *Count* and *spend* are different questions and get different answers. The
  screen's «Заказов всего» is every order the customer placed, cancellations
  included — they placed them, and a total that quietly omitted one would not
  match the filter chips beside it. «Потрачено» is money that actually moved.
* Money that moved means ``paid_at`` is set and the order was not refunded. A
  measured timestamp rather than a status guess: an order can reach half a dozen
  states after payment, and a list of which of them count is a list that goes
  stale the next time one is added. Refunds are excluded because the money came
  back, and counting it would also let anybody climb the loyalty ladder by
  placing orders and asking for them back.
* Savings come out of the *pinned* breakdown, never recomputed. The stored figure
  is what was charged (ADR-0002); a fresh calculation under today's rates would
  produce a different number and describe nothing that happened.
* Lead time is measured from payment to dispatch, over dispatched orders only.
  An order still printing has no lead time yet, and averaging in a zero would
  make a busy farm look faster than an idle one.
* Punctuality is counted only over orders that carried a promise. An order placed
  before promising existed is not late; it is unmeasured.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.ordering.policies import OrderStatus
from printorian.contexts.ordering.schemas import Lifetime, MonthPoint
from printorian.contexts.pricing import ADJUSTMENT_CUSTOMER_DISCOUNT, ADJUSTMENT_VOLUME_DISCOUNT
from printorian.core.clock import Clock
from printorian.core.ids import EntityId

#: Money that came back is not money spent.
NOT_REVENUE = frozenset({OrderStatus.REFUNDED})

#: The discount lines that make up «Сэкономлено». Both are negative amounts.
SAVING_CODES = frozenset({ADJUSTMENT_VOLUME_DISCOUNT, ADJUSTMENT_CUSTOMER_DISCOUNT})

#: Columns on the activity chart. Twelve, ending with the current month.
MONTHS_SHOWN = 12

_SECONDS_PER_DAY = Decimal(86_400)


async def lifetime(db: AsyncSession, clock: Clock, customer_id: EntityId) -> Lifetime:
    """Every figure the account header shows, from one pass over the orders.

    One query rather than six aggregates: a customer has tens of orders, not
    millions, and six round trips to compute a header is worse than reading the
    rows and counting them here — where the counting rules above are visible
    rather than spread across six `WHERE` clauses.
    """
    orders = list(
        await db.scalars(
            select(Order).where(Order.customer_id == customer_id).order_by(Order.created_at)
        )
    )
    revenue = [order for order in orders if _is_revenue(order)]

    spend = sum((order.total for order in revenue), Decimal(0))
    saved = sum((_saved_on(order.price_breakdown) for order in revenue), Decimal(0))

    # Unpacked into plain pairs rather than filtered in place, so the optional
    # timestamps are narrowed once instead of at every use. The alternative was
    # three `# type: ignore` comments over arithmetic that is only safe because of
    # a condition several lines above it.
    runs = [
        (order.paid_at, order.shipped_at, order.promised_at)
        for order in revenue
        if order.paid_at is not None and order.shipped_at is not None
    ]
    days = [
        Decimal((shipped - paid).total_seconds()) / _SECONDS_PER_DAY for paid, shipped, _ in runs
    ]
    promised = [(shipped, due) for _, shipped, due in runs if due is not None]

    return Lifetime(
        orders=len(orders),
        in_progress=sum(1 for order in orders if order.status.counts_against_sla),
        spend=spend,
        average_order=_mean([order.total for order in revenue], places="0.01"),
        saved=saved,
        average_days=_mean(days, places="0.1"),
        on_time=sum(1 for shipped, due in promised if shipped <= due),
        on_time_of=len(promised),
        months=_months(orders, clock),
    )


def _is_revenue(order: Order) -> bool:
    """Whether this order's money actually moved and stayed moved."""
    return order.paid_at is not None and order.status not in NOT_REVENUE


async def spent(db: AsyncSession, customer_id: EntityId) -> Decimal:
    """Lifetime spend alone — what the loyalty ladder is read against.

    Separate from :func:`lifetime` because the order router needs exactly this
    number on the hot path of placing an order, and loading every row of a
    customer's history to price one line would be a poor trade. Same rule as
    :func:`lifetime`'s «Потрачено», stated in SQL rather than in Python: money
    that moved and stayed moved.

    Note what this means for the order being placed right now — it has no
    `paid_at`, so it does not count towards its own price. A customer does not
    cross into Silver partway through checking out, which is the only version of
    this that can be quoted before the order exists.
    """
    total = await db.scalar(
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.customer_id == customer_id,
            Order.paid_at.is_not(None),
            Order.status.notin_(list(NOT_REVENUE)),
        )
    )
    return Decimal(str(total or 0))


async def lines_per_asset(db: AsyncSession, customer_id: EntityId) -> dict[EntityId, int]:
    """How many order lines each of this customer's uploads has been printed from.

    The «ЗАКАЗОВ 4» on a model card. Counted over the customer's own orders only —
    an upload that two people happen to share is deduplicated by content address
    (`ModelAsset.sha256`), and telling one of them how often the other ordered it
    would leak across accounts.
    """
    rows = await db.execute(
        select(OrderLine.model_asset_id, func.count())
        .join(Order, Order.id == OrderLine.order_id)
        .where(Order.customer_id == customer_id, OrderLine.model_asset_id.is_not(None))
        .group_by(OrderLine.model_asset_id)
    )
    return {asset_id: int(count) for asset_id, count in rows if asset_id is not None}


async def order_numbers(db: AsyncSession, customer_id: EntityId) -> dict[EntityId, str]:
    """This customer's orders, id to number — what the receipts list is built on.

    Both halves are needed and neither alone will do: payments key on the id, and
    a receipt that names one is a receipt nobody can match to the order they are
    looking at.
    """
    rows = await db.execute(
        select(Order.id, Order.number)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
    )
    # `t.tuple()` rather than a bare `dict(rows)`: a SQLAlchemy `Row` is a
    # sequence of two and converts happily, but its declared type is not
    # `tuple[UUID, str]`, so the plain form needs a `type: ignore` and the
    # comprehension form is what `ruff` objects to. Asking for tuples is the
    # version both tools agree with.
    return dict(rows.tuples().all())


def _saved_on(breakdown: dict[str, Any]) -> Decimal:
    """The discount lines of one pinned breakdown, as a positive figure.

    Tolerant of an order with no breakdown at all. Those exist — an order placed
    before snapshots were persisted — and a header that raises on one would be a
    header nobody with a long history can load.
    """
    total = Decimal(0)
    for line in breakdown.get("lines") or ():
        if not isinstance(line, dict) or line.get("code") not in SAVING_CODES:
            continue
        try:
            total += Decimal(str(line.get("amount", "0")))
        except (ArithmeticError, ValueError):
            continue
    return -total if total < 0 else total


def _mean(values: list[Decimal], *, places: str) -> Decimal | None:
    """The average, or ``None`` when there is nothing to average."""
    if not values:
        return None
    return (sum(values, Decimal(0)) / len(values)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _months(orders: list[Order], clock: Clock) -> list[MonthPoint]:
    """Twelve columns ending with the current one, zeroes included.

    The zeroes are the point. A chart drawn only from months that had orders
    would compress a quiet summer into nothing and show a flat line where the
    truth is a gap.
    """
    now = clock.now()
    keys: list[str] = []
    year, month = now.year, now.month
    for _ in range(MONTHS_SHOWN):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    keys.reverse()

    tally = dict.fromkeys(keys, 0)
    for order in orders:
        key = f"{order.created_at.year:04d}-{order.created_at.month:02d}"
        if key in tally:
            tally[key] += 1
    return [MonthPoint(month=key, orders=tally[key]) for key in keys]
