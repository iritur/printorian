"""Reading the summarised hours back — and what happens where there is no row.

Most of these are one question in different clothes: *does an hour nobody measured
stay distinguishable from an hour in which nothing happened?* ADR-0007 says it must,
and the reader is the last place that can still be true — past this the numbers are
JSON, `Number(null)` is 0, and `?? 0` is one keystroke.

The window rule is next door in `test_fleet_measure_window.py`; the dashboard's two
reshapings are in `test_fleet_occupancy.py`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.measures import (
    Grain,
    MetricWindow,
    fleet_buckets,
    printer_buckets,
)
from printorian.core.ids import new_id
from tests.unit._measure_support import FULL_HOUR, HOUR, summarised


def window(hours: int, grain: Grain = Grain.HOUR) -> MetricWindow:
    return MetricWindow(since=HOUR, until=HOUR + timedelta(hours=hours), grain=grain)


# ------------------------------------------------------------------ the ruler


async def test_the_grid_is_dense_and_ascending(db_session: AsyncSession) -> None:
    """One entry per hour of the window, whatever the summary happens to hold.

    Dense on the server so every consumer gets the same ruler. A sparse array
    pushes gap-filling to the client, where each panel would do it differently and
    at least one of them would fill with zero.
    """
    printer = new_id()
    await summarised(db_session, printer, HOUR, printing_seconds=FULL_HOUR)
    await summarised(db_session, printer, HOUR + timedelta(hours=3), idle_seconds=FULL_HOUR)

    buckets = await fleet_buckets(db_session, window(6))

    assert len(buckets) == 6
    assert [b.bucket_start for b in buckets] == [HOUR + timedelta(hours=n) for n in range(6)]


async def test_an_hour_nobody_summarised_is_all_null_and_never_zero(
    db_session: AsyncSession,
) -> None:
    """The ADR-0007 test for the read side.

    ``idle_seconds: 0`` for an hour nobody polled is how an unpolled night becomes
    an idle night — a number the panel would draw, brightly and wrongly.
    """
    printer = new_id()
    await summarised(db_session, printer, HOUR, printing_seconds=FULL_HOUR)

    empty = (await fleet_buckets(db_session, window(2)))[1]

    assert empty.observed_seconds is None
    assert empty.idle_seconds is None
    assert empty.printing_seconds is None
    assert empty.offline_seconds is None
    assert empty.state_changes is None
    assert empty.printers_reporting is None
    assert empty.load is None


async def test_a_real_bucket_keeps_its_genuine_zeroes(db_session: AsyncSession) -> None:
    """A partly-covered hour is a *measurement*, and its zeroes are measurements too.

    Ten minutes observed, all of it printing: idle really was zero for the part
    anybody saw, and it is `observed_seconds` — not 3600 — that keeps the ratio
    honest about the other fifty.
    """
    printer = new_id()
    await summarised(
        db_session, printer, HOUR, observed_seconds=Decimal(600), printing_seconds=Decimal(600)
    )

    bucket = (await fleet_buckets(db_session, window(1)))[0]

    assert bucket.idle_seconds == Decimal(0)
    assert bucket.observed_seconds == Decimal(600)
    assert bucket.load == Decimal("1.00")


# ------------------------------------------------------------- the denominator


async def test_load_divides_by_what_was_observed_and_not_by_the_hour(
    db_session: AsyncSession,
) -> None:
    """One machine of two reported, and it printed throughout.

    The honest cell is 100% of what was measured, with `printers_reporting` saying
    the measurement covered one machine. The naive figure — printing over
    ``3600 × roster`` — reads 50% by asserting the silent machine was idle, which
    is the invented reading wearing a percentage sign.
    """
    reporting, silent = new_id(), new_id()
    await summarised(db_session, reporting, HOUR, printing_seconds=FULL_HOUR)
    await summarised(db_session, silent, HOUR + timedelta(hours=1), idle_seconds=FULL_HOUR)

    bucket = (await fleet_buckets(db_session, window(1)))[0]

    assert bucket.load == Decimal("1.00")
    assert bucket.printers_reporting == 1


async def test_an_hour_that_observed_nothing_has_no_load(db_session: AsyncSession) -> None:
    """No denominator, no ratio. Zero would be a claim about a machine nobody saw."""
    printer = new_id()
    await summarised(db_session, printer, HOUR, observed_seconds=Decimal(0))

    bucket = (await fleet_buckets(db_session, window(1)))[0]

    assert bucket.observed_seconds == Decimal(0)
    assert bucket.load is None


async def test_printers_reporting_counts_distinct_machines(db_session: AsyncSession) -> None:
    first, second = new_id(), new_id()
    await summarised(db_session, first, HOUR, printing_seconds=FULL_HOUR)
    await summarised(db_session, second, HOUR, idle_seconds=FULL_HOUR)

    bucket = (await fleet_buckets(db_session, window(1)))[0]

    assert bucket.printers_reporting == 2
    assert bucket.observed_seconds == Decimal(7200)
    assert bucket.load == Decimal("0.50")


# ------------------------------------------------------------------- the total


async def test_the_total_equals_the_sum_of_the_hours_beside_it(
    db_session: AsyncSession,
) -> None:
    """«Наработка за сутки» against the cells it sits next to.

    Both grains come out of one statement builder with a different group key, so
    this is an equality rather than an approximation — and it is the first thing
    anyone checks when a tile disagrees with the grid under it.
    """
    printer = new_id()
    for offset, printing in enumerate((FULL_HOUR, Decimal(1800), Decimal(0))):
        await summarised(
            db_session,
            printer,
            HOUR + timedelta(hours=offset),
            printing_seconds=printing,
            idle_seconds=FULL_HOUR - printing,
        )

    hourly = await fleet_buckets(db_session, window(3))
    totals = await fleet_buckets(db_session, window(3, Grain.TOTAL))

    assert len(totals) == 1
    total = totals[0]
    assert total.bucket_start == HOUR
    assert total.printing_seconds == sum(b.printing_seconds or 0 for b in hourly)
    assert total.observed_seconds == sum(b.observed_seconds or 0 for b in hourly)
    assert total.load == Decimal("0.50")


async def test_a_total_over_a_window_with_no_rows_is_one_all_null_bucket(
    db_session: AsyncSession,
) -> None:
    """Exactly one entry, and it claims nothing."""
    total = await fleet_buckets(db_session, window(24, Grain.TOTAL))

    assert len(total) == 1
    assert total[0].bucket_start == HOUR
    assert total[0].observed_seconds is None
    assert total[0].load is None


# ------------------------------------------------------- one machine's own fields


async def test_a_null_temperature_stays_null_rather_than_becoming_a_zero(
    db_session: AsyncSession,
) -> None:
    """`samples.sample_of` refuses to invent a cold bed and this must not undo it.

    A column of zeroes is indistinguishable from a genuinely cold bed once the
    readings are old enough that nobody remembers which it was.
    """
    printer = new_id()
    await summarised(db_session, printer, HOUR, printing_seconds=FULL_HOUR)

    bucket = (await printer_buckets(db_session, window(1), printer_id=printer))[0]

    assert bucket.nozzle_temp_avg_c is None
    assert bucket.nozzle_temp_max_c is None
    assert bucket.bed_temp_avg_c is None
    assert bucket.bed_temp_max_c is None
    # ...and the hour itself is real, which is what makes the nulls mean "not
    # measured" rather than "not summarised".
    assert bucket.observed_seconds == FULL_HOUR


async def test_temperatures_pass_through_untouched_at_hour_grain(
    db_session: AsyncSession,
) -> None:
    printer = new_id()
    await summarised(
        db_session,
        printer,
        HOUR,
        printing_seconds=FULL_HOUR,
        nozzle_temp_avg_c=Decimal("221.50"),
        nozzle_temp_max_c=Decimal("255.00"),
        bed_temp_avg_c=Decimal("59.90"),
        bed_temp_max_c=Decimal("70.00"),
    )

    bucket = (await printer_buckets(db_session, window(1), printer_id=printer))[0]

    assert bucket.nozzle_temp_avg_c == Decimal("221.50")
    assert bucket.nozzle_temp_max_c == Decimal("255.00")
    assert bucket.bed_temp_avg_c == Decimal("59.90")


async def test_the_average_is_absent_at_total_grain_and_the_maximum_is_not(
    db_session: AsyncSession,
) -> None:
    """An average of hourly averages is not the average.

    The stored figure is over the samples that *had* a reading, and the row carries
    no count of those, so there is no correct weight. Absent rather than
    approximated. The maxima have no such problem: the largest of the hourly
    largest is the largest.
    """
    printer = new_id()
    for offset, peak in enumerate((Decimal(240), Decimal(255))):
        await summarised(
            db_session,
            printer,
            HOUR + timedelta(hours=offset),
            printing_seconds=FULL_HOUR,
            nozzle_temp_avg_c=Decimal(200) + peak,
            nozzle_temp_max_c=peak,
            bed_temp_avg_c=Decimal(55),
            bed_temp_max_c=Decimal(70),
        )

    total = (await printer_buckets(db_session, window(2, Grain.TOTAL), printer_id=printer))[0]

    assert total.nozzle_temp_avg_c is None
    assert total.bed_temp_avg_c is None
    assert total.nozzle_temp_max_c == Decimal(255)
    assert total.bed_temp_max_c == Decimal(70)


async def test_a_summarised_hour_with_no_codes_is_empty_and_a_gap_is_null(
    db_session: AsyncSession,
) -> None:
    """Two different claims, and ``null`` is only allowed to carry one of them."""
    printer = new_id()
    await summarised(db_session, printer, HOUR, printing_seconds=FULL_HOUR)

    clean, missing = await printer_buckets(db_session, window(2), printer_id=printer)

    assert clean.error_codes == {}
    assert missing.error_codes is None


async def test_error_codes_merge_by_summing_per_key(db_session: AsyncSession) -> None:
    """«HMS_0300 ×14 за сутки» — what turns an alert row from a state into a pattern."""
    printer = new_id()
    await summarised(
        db_session,
        printer,
        HOUR,
        error_seconds=FULL_HOUR,
        error_sample_count=9,
        error_codes={"HMS_0300": 8, "HMS_0C00": 1},
    )
    await summarised(
        db_session,
        printer,
        HOUR + timedelta(hours=1),
        error_seconds=FULL_HOUR,
        error_sample_count=6,
        error_codes={"HMS_0300": 6},
    )

    total = (await printer_buckets(db_session, window(2, Grain.TOTAL), printer_id=printer))[0]

    assert total.error_codes == {"HMS_0300": 14, "HMS_0C00": 1}
    assert total.error_sample_count == 15


async def test_no_samples_in_a_summarised_hour_is_zero_and_not_absent(
    db_session: AsyncSession,
) -> None:
    """A machine polled at 09:59:58 and not again until 10:15 carries seconds into
    the 10:00 bucket while contributing nothing to its counts. That hour was
    summarised, and saying so is the difference between "we looked" and "we did
    not"."""
    printer = new_id()
    await summarised(
        db_session,
        printer,
        HOUR,
        observed_seconds=Decimal(15),
        printing_seconds=Decimal(15),
        sample_count=0,
    )

    measured, absent = await printer_buckets(db_session, window(2), printer_id=printer)

    assert measured.sample_count == 0
    assert absent.sample_count is None


async def test_one_machines_history_excludes_the_rest_of_the_farm(
    db_session: AsyncSession,
) -> None:
    mine, theirs = new_id(), new_id()
    await summarised(db_session, mine, HOUR, printing_seconds=Decimal(1800))
    await summarised(db_session, theirs, HOUR, printing_seconds=FULL_HOUR)

    bucket = (await printer_buckets(db_session, window(1), printer_id=mine))[0]

    assert bucket.printing_seconds == Decimal(1800)
    assert bucket.observed_seconds == FULL_HOUR


async def test_an_unknown_machine_gets_a_dense_grid_of_nothing(
    db_session: AsyncSession,
) -> None:
    """Which is precisely why the route checks the registry first.

    Read on its own this is the correct answer — the reader cannot tell a retired
    machine from a typo. Served on its own it renders as "this machine did
    nothing", so the API layer refuses the id before it ever gets here.
    """
    await summarised(db_session, new_id(), HOUR, printing_seconds=FULL_HOUR)

    buckets = await printer_buckets(db_session, window(3), printer_id=new_id())

    assert len(buckets) == 3
    assert all(bucket.observed_seconds is None for bucket in buckets)
