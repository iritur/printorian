"""The dashboard's measuring vocabulary: a window, and a figure against its past.

Shared by the orders row (`overview.py`) and the finance row (`finance.py`) so the
two are cut against the same boundary. Two panels on one screen computing their
own "previous period" is how a dashboard ends up saying revenue is up and orders
are down about the same fortnight.

**Every figure comes with its previous-period counterpart.** A dashboard number
without one is a number nobody can act on: 248 orders is good or bad only against
227. Computing the comparison here rather than in the client means the two windows
are always the same length — the classic "month-to-date against the whole previous
month" error cannot be made.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class Period(StrEnum):
    """The window the dashboard's figures are taken over."""

    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"


class Window(BaseModel):
    """One period and the one before it, cut to the same length."""

    period: Period
    start: datetime
    end: datetime
    previous_start: datetime


class Trend(BaseModel):
    """A figure against its own previous-period value.

    ``change_percent`` is ``None`` rather than zero when there is nothing to
    compare against. A farm's first week showing "+0%" would be a lie about a
    comparison that was never made.
    """

    value: Decimal
    previous: Decimal
    change_percent: Decimal | None = None


def window_for(period: Period, now: datetime) -> Window:
    """The period's bounds, and the equally long window before it.

    Cut in UTC, matching everything else stored. Presenting them in the farm's
    timezone is the client's job — the backend emits instants, never wall clocks.
    """
    end = now
    if period is Period.TODAY:
        start = midnight_of(now)
    elif period is Period.WEEK:
        start = midnight_of(now) - timedelta(days=now.weekday())
    elif period is Period.MONTH:
        start = midnight_of(now).replace(day=1)
    else:
        first_month = ((now.month - 1) // 3) * 3 + 1
        start = midnight_of(now).replace(month=first_month, day=1)
    # Same length, immediately before. Not "last calendar month": comparing 19 days
    # of August against the whole of July is the error this exists to prevent.
    return Window(period=period, start=start, end=end, previous_start=start - (end - start))


def month_window(now: datetime) -> Window:
    """This calendar month against the whole of the previous one.

    The one place the equal-length rule is deliberately not applied. The kit's
    "За месяц" tile compares a running month with a finished one — that is what
    the reader means by "last month", and forcing equal lengths here would
    compare August-so-far with a fortnight of July that nobody thinks in.
    """
    start = midnight_of(now).replace(day=1)
    previous_start = midnight_of(start - timedelta(days=1)).replace(day=1)
    return Window(period=Period.MONTH, start=start, end=now, previous_start=previous_start)


def midnight_of(moment: datetime) -> datetime:
    """The start of ``moment``'s UTC day."""
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


async def trend_of(db: AsyncSession, window: Window, aggregate: Any, column: Any) -> Trend:
    """One aggregate over the window, and the same one over the window before it."""
    value = await sum_between(db, aggregate, column, window.start, window.end)
    previous = await sum_between(db, aggregate, column, window.previous_start, window.start)
    return as_trend(value, previous)


async def sum_between(
    db: AsyncSession, aggregate: Any, column: Any, start: datetime, end: datetime
) -> Decimal:
    """One aggregate, over rows whose ``column`` falls in ``[start, end)``."""
    value = await db.scalar(select(aggregate).where(column >= start, column < end))
    return Decimal(str(value)) if value is not None else Decimal(0)


def as_trend(value: Decimal, previous: Decimal) -> Trend:
    return Trend(value=value, previous=previous, change_percent=change_percent(value, previous))


def change_percent(value: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return ((value - previous) / previous * 100).quantize(Decimal("0.1"))


def share(part: Decimal, whole: Decimal) -> Decimal:
    """``part`` as a percentage of ``whole``; zero when there is no whole."""
    if whole == 0:
        return Decimal(0)
    return (part / whole * 100).quantize(Decimal("0.1"))


__all__ = [
    "Period",
    "Trend",
    "Window",
    "as_trend",
    "change_percent",
    "midnight_of",
    "month_window",
    "share",
    "sum_between",
    "trend_of",
    "window_for",
]
