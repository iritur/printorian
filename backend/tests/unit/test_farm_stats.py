"""Public figures the farm can support.

The promo page argues that this shop publishes real numbers with the method
behind them, which makes one failure mode worse than being wrong: being
*plausible*. Every test here is about the difference between "we measured none"
and "we have not measured yet".
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.api.farm_stats import farm_stats
from printorian.contexts.ordering.models import Order
from printorian.contexts.ordering.policies import OrderStatus
from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


#: UUIDv7 leads with a millisecond timestamp, so `hex[:6]` collides for anything
#: created in the same tick. A counter is simply correct.
_numbers = itertools.count(1)


def an_order(
    *,
    shipped: datetime | None,
    promised: datetime | None,
    status: OrderStatus = OrderStatus.SHIPPED,
) -> Order:
    return Order(
        number=f"PR-{next(_numbers):06d}",
        status=status,
        customer_email="buyer@example.com",
        currency="RUB",
        total=Decimal(1000),
        promised_at=promised,
        shipped_at=shipped,
        price_breakdown={},
        engine_version="1.0.0",
    )


def a_job(
    *,
    started: datetime | None,
    finished: datetime | None,
    status: JobStatus,
    order_id: object,
) -> PrintJob:
    """One job of a real order.

    `order_id` is required rather than fabricated: the foreign key is enforced
    on PostgreSQL, and these statistics are computed by joining jobs to the
    orders they belong to.
    """
    return PrintJob(
        order_id=order_id,  # type: ignore[arg-type]
        status=status,
        material_type="PLA",
        colors=["#FFFFFF"],
        width_mm=Decimal(40),
        depth_mm=Decimal(40),
        height_mm=Decimal(40),
        grams_required=Decimal(20),
        estimated_minutes=Decimal(60),
        started_at=started,
        finished_at=finished,
    )


async def test_a_farm_with_no_history_says_nothing(db_session: AsyncSession) -> None:
    """`None`, not zero.

    A brand-new farm reporting "0% late, 0% failures" would be claiming a
    measurement it has never taken — the exact defect this page exists to argue
    against.
    """
    stats = await farm_stats(db_session, now=NOW)

    assert stats.orders_delivered == 0
    assert stats.on_time_percent is None
    assert stats.failure_percent is None
    assert stats.print_hours is None
    assert not stats.has_history


async def test_on_time_counts_only_orders_that_were_promised(db_session: AsyncSession) -> None:
    """An order with no promise cannot be late.

    Counting it as on time would credit the farm for having said nothing, which
    is the easiest way to manufacture a good number.
    """
    db_session.add_all(
        [
            an_order(shipped=NOW - timedelta(days=2), promised=NOW - timedelta(days=1)),  # on time
            an_order(shipped=NOW - timedelta(days=1), promised=NOW - timedelta(days=3)),  # late
            an_order(shipped=NOW - timedelta(days=1), promised=None),  # no promise
        ]
    )
    await db_session.flush()

    stats = await farm_stats(db_session, now=NOW)

    assert stats.orders_delivered == 3
    # One on time out of the two that carried a promise — not one out of three.
    assert stats.on_time_percent == Decimal("50.0")


async def test_work_in_progress_is_not_counted_as_delivered(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            an_order(shipped=NOW - timedelta(days=1), promised=NOW, status=OrderStatus.SHIPPED),
            an_order(shipped=None, promised=NOW, status=OrderStatus.PRINTING),
        ]
    )
    await db_session.flush()

    assert (await farm_stats(db_session, now=NOW)).orders_delivered == 1


async def test_older_than_the_window_is_excluded(db_session: AsyncSession) -> None:
    """The figure describes the farm as it is now, not as it once was."""
    db_session.add_all(
        [
            an_order(shipped=NOW - timedelta(days=10), promised=NOW - timedelta(days=9)),
            an_order(shipped=NOW - timedelta(days=200), promised=NOW - timedelta(days=199)),
        ]
    )
    await db_session.flush()

    assert (await farm_stats(db_session, now=NOW)).orders_delivered == 1


async def test_failure_share_is_over_finished_jobs(db_session: AsyncSession) -> None:
    """A job still printing is not evidence either way."""
    order = an_order(shipped=None, promised=None)
    db_session.add(order)
    await db_session.flush()
    db_session.add_all(
        [
            a_job(
                order_id=order.id,
                started=NOW - timedelta(hours=4),
                finished=NOW - timedelta(hours=1),
                status=JobStatus.SUCCEEDED,
            ),
            a_job(
                order_id=order.id,
                started=NOW - timedelta(hours=6),
                finished=NOW - timedelta(hours=5),
                status=JobStatus.FAILED,
            ),
            a_job(
                order_id=order.id,
                started=NOW - timedelta(hours=1),
                finished=None,
                status=JobStatus.PRINTING,
            ),
        ]
    )
    await db_session.flush()

    stats = await farm_stats(db_session, now=NOW)

    assert stats.failure_percent == Decimal("50.0")
    # 3 hours plus 1 hour; the unfinished job contributes nothing rather than
    # counting its time so far, which would keep growing on a stuck print.
    assert stats.print_hours == Decimal("4.0")


@pytest.mark.parametrize("window", [timedelta(days=7), timedelta(days=365)])
async def test_the_window_is_reported_with_the_figures(
    db_session: AsyncSession, window: timedelta
) -> None:
    """A percentage without its period is not a fact anyone can check."""
    stats = await farm_stats(db_session, now=NOW, window=window)
    assert stats.window_days == window.days
