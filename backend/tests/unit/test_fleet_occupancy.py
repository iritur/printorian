"""The dashboard's two reshapings of the measured hours.

The load map and the KPI tiles used to come from `print_jobs` — booked machine
time, not run machine time — and the numbers a person has been looking at change
because of it. What is pinned here is the part that must not change again: an hour
nobody summarised is *blank*, not dark, in both shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.occupancy import HEAT_DAYS, hourly_load, occupancy
from printorian.core.ids import new_id
from tests.unit._measure_support import FULL_HOUR, summarised

#: Mid-afternoon on a Tuesday, so "today" is the last row of the grid and there are
#: hours ahead of `now` in it that nobody could have summarised yet.
NOW = datetime(2026, 3, 3, 14, 20, tzinfo=UTC)
TODAY = datetime(2026, 3, 3, 0, 0, tzinfo=UTC)


# ------------------------------------------------------------------ the load map


async def test_the_grid_is_seven_rows_of_twenty_four_whatever_the_farm_did(
    db_session: AsyncSession,
) -> None:
    """Its point is the shape, so the shape is not allowed to depend on the data."""
    rows = await hourly_load(db_session, now=NOW)

    assert len(rows) == HEAT_DAYS
    assert all(len(row.hours) == 24 for row in rows)
    assert [row.weekday for row in rows][-1] == NOW.weekday()


async def test_a_cell_is_printing_over_observed_and_not_over_the_roster(
    db_session: AsyncSession,
) -> None:
    """One machine printed the whole hour; a second was never polled in it.

    The cell reads full, because full is what was measured, and
    `printers_reporting` is the figure that stops that from being flattering —
    a cell at 100% with one of two machines reporting must not look like 100% with
    two of two.
    """
    busy, silent = new_id(), new_id()
    at_nine = TODAY + timedelta(hours=9)
    await summarised(db_session, busy, at_nine, printing_seconds=FULL_HOUR)
    await summarised(db_session, silent, at_nine - timedelta(hours=1), idle_seconds=FULL_HOUR)

    today = (await hourly_load(db_session, now=NOW))[-1]

    assert today.hours[9].load == Decimal("1.00")
    assert today.hours[9].printers_reporting == 1
    assert today.hours[8].load == Decimal("0.00")
    assert today.hours[8].printers_reporting == 1


async def test_an_unpolled_hour_is_blank_and_not_dark(db_session: AsyncSession) -> None:
    """The visible ADR-0007 change, and the one that belongs in a release note.

    An operator who has read "dark = idle" for months must now read a third cell
    treatment as "unknown". A zero here would silently restore the old, wrong
    reading in the one place nobody would look for it.
    """
    printer = new_id()
    await summarised(db_session, printer, TODAY + timedelta(hours=9), printing_seconds=FULL_HOUR)

    today = (await hourly_load(db_session, now=NOW))[-1]

    assert today.hours[3].load is None
    assert today.hours[3].printers_reporting == 0
    # The hours ahead of `now` have not happened, and the open hour is never
    # summarised, so they are blank for the same reason rather than empty-looking.
    assert today.hours[20].load is None


async def test_finished_plates_sitting_on_the_bed_are_not_printing(
    db_session: AsyncSession,
) -> None:
    """The change that will make night cells drop hard on some farms.

    `finished_seconds` is its own column and is not printing — where the old
    job-derived map lit the cell for as long as the job row stayed open.
    """
    printer = new_id()
    hour = TODAY + timedelta(hours=2)
    await summarised(db_session, printer, hour, printing_seconds=Decimal(0))

    today = (await hourly_load(db_session, now=NOW))[-1]

    assert today.hours[2].load == Decimal("0.00")
    assert today.hours[2].printers_reporting == 1


# ------------------------------------------------------------------- the tiles


async def test_run_and_idle_are_both_measured_and_neither_is_a_residual(
    db_session: AsyncSession,
) -> None:
    """`idle = capacity - run` was the old arrangement, and its bugs were structural:
    a job that ran past `THROUGHPUT_LIMIT`, or never closed, silently inflated idle.
    Here both are columns."""
    printer = new_id()
    for offset, printing in enumerate((FULL_HOUR, Decimal(1800))):
        await summarised(
            db_session,
            printer,
            TODAY + timedelta(hours=offset),
            printing_seconds=printing,
            idle_seconds=FULL_HOUR - printing,
        )

    measured = await occupancy(db_session, since=TODAY, until=NOW)

    assert measured.printing_hours == Decimal("1.5")
    assert measured.idle_hours == Decimal("0.5")
    assert measured.observed_hours == Decimal("2.0")
    assert measured.load == Decimal("0.75")


async def test_the_denominator_is_observed_hours_and_not_the_roster(
    db_session: AsyncSession,
) -> None:
    """«ИЗ 288 ВОЗМОЖНЫХ» becomes what was observed.

    Two machines, one of which reported for a single hour of the window: possible
    hours are what was seen, not machines × 24, so the tile stops asserting that
    machines nobody polled were available.
    """
    first, second = new_id(), new_id()
    await summarised(db_session, first, TODAY, printing_seconds=FULL_HOUR)
    await summarised(db_session, second, TODAY, idle_seconds=FULL_HOUR)
    await summarised(db_session, first, TODAY + timedelta(hours=1), printing_seconds=FULL_HOUR)

    measured = await occupancy(db_session, since=TODAY, until=NOW)

    assert measured.observed_hours == Decimal("3.0")
    assert measured.printers_reporting == 2


async def test_a_period_with_no_closed_hour_reports_nothing_rather_than_zero(
    db_session: AsyncSession,
) -> None:
    """The dashboard opened at 00:30 with `period=today`.

    Nothing has been summarised, the summary must still render, and every figure is
    absent. Zero hours observed would be a measurement nobody made — and the tile
    would show a farm that did nothing overnight.
    """
    just_after_midnight = TODAY + timedelta(minutes=30)

    measured = await occupancy(db_session, since=TODAY, until=just_after_midnight)

    assert measured.observed_hours is None
    assert measured.idle_hours is None
    assert measured.load is None
    assert measured.printers_reporting is None


async def test_the_previous_period_is_read_by_the_same_function(
    db_session: AsyncSession,
) -> None:
    """What puts something behind the `hv-kpi__d` delta chip, which has had nothing.

    Same reader, different window — so the comparison cannot be against a figure
    computed a second way.
    """
    printer = new_id()
    yesterday = TODAY - timedelta(days=1)
    await summarised(db_session, printer, yesterday, printing_seconds=Decimal(900))
    await summarised(db_session, printer, TODAY, printing_seconds=FULL_HOUR)

    current = await occupancy(db_session, since=TODAY, until=NOW)
    previous = await occupancy(db_session, since=yesterday, until=TODAY)

    assert current.load == Decimal("1.00")
    assert previous.load == Decimal("0.25")
