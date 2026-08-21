"""The hour, summarised — the arithmetic behind every number in a rollup row.

Every case here is really one question: *can this row be trusted once the samples
behind it are gone?* The answer has to survive a poller that stopped mid-hour, a
span that crossed midnight-of-the-hour, and a machine that reported an error
while printing perfectly well.

The most important test in the file is
`test_an_unpolled_stretch_is_attributed_to_no_state_at_all`. Everything else
checks that a number is right; that one checks that a number the farm does not
know is not invented — ADR-0007 applied to the summary rather than to the reading.

The window and idempotency halves live in `test_rollup_window.py`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.history import STATE_COLUMNS, MetricRollup
from printorian.core.ids import new_id
from printorian.drivers import PrinterState
from tests.unit._rollup_support import (
    HOUR,
    PREVIOUS_HOUR,
    a_full_hour,
    bucket,
    record,
    summarise,
)

# ------------------------------------------------------------ the arithmetic


async def test_an_hour_of_printing_is_a_full_hour_of_printing(db_session: AsyncSession) -> None:
    """3600 seconds *exactly*, which is the whole point of the carry-in rule.

    Attributing each sample the interval to the next one and stopping there leaves
    every hour five seconds short, and `observed_seconds == 3600` degrades from an
    equality worth alerting on into a fuzzy comparison nobody can act on.
    """
    printer = new_id()
    await a_full_hour(db_session, printer)

    await summarise(db_session)

    row = await bucket(db_session, printer, HOUR)
    assert row.printing_seconds == Decimal(3600)
    assert row.observed_seconds == Decimal(3600)
    assert row.sample_count == 720
    assert row.state_changes == 0


async def test_a_state_change_mid_hour_splits_the_durations(db_session: AsyncSession) -> None:
    printer = new_id()
    await record(db_session, printer, first=HOUR, count=360, state=PrinterState.PRINTING)
    await record(
        db_session,
        printer,
        first=HOUR + timedelta(minutes=30),
        count=361,
        state=PrinterState.IDLE,
    )

    await summarise(db_session)

    row = await bucket(db_session, printer, HOUR)
    assert row.printing_seconds == Decimal(1800)
    assert row.idle_seconds == Decimal(1800)
    assert row.observed_seconds == Decimal(3600)
    assert row.state_changes == 1


async def test_the_eight_durations_sum_to_the_observed_seconds(db_session: AsyncSession) -> None:
    """The invariant no CHECK constraint carries, because rounding makes it brittle
    in SQL and exact on any data a real poll produces."""
    printer = new_id()
    await record(db_session, printer, first=HOUR, count=240, state=PrinterState.PREPARING)
    await record(
        db_session, printer, first=HOUR + timedelta(minutes=20), count=240, state=PrinterState.ERROR
    )
    await record(
        db_session,
        printer,
        first=HOUR + timedelta(minutes=40),
        count=241,
        state=PrinterState.MAINTENANCE,
    )

    await summarise(db_session)

    row = await bucket(db_session, printer, HOUR)
    total = sum(getattr(row, column) for column in STATE_COLUMNS.values())
    assert total == row.observed_seconds == Decimal(3600)
    assert row.preparing_seconds == row.error_seconds == row.maintenance_seconds == Decimal(1200)


async def test_an_unpolled_stretch_is_attributed_to_no_state_at_all(
    db_session: AsyncSession,
) -> None:
    """The ADR-0007 test, and the one that makes every other figure honest.

    The poller stops for ten minutes and five seconds. Fifteen of those seconds are
    the gap ceiling — the last sample still speaks for three polls' worth — and the
    remaining 600 belong to *nothing*: not idle, not offline, not printing. They
    come off `observed_seconds`, which is the denominator of every percentage the
    dashboard draws, so an hour that was three-quarters unobserved cannot report
    itself as three-quarters idle.
    """
    printer = new_id()
    # 10:00:00 → 10:09:45, then silence until 10:20:00.
    await record(db_session, printer, first=HOUR, count=118)
    await record(db_session, printer, first=HOUR + timedelta(minutes=20), count=480)
    await record(db_session, printer, first=HOUR + timedelta(hours=1), count=1)

    await summarise(db_session)

    row = await bucket(db_session, printer, HOUR)
    assert row.observed_seconds == Decimal(3000)
    assert row.printing_seconds == Decimal(3000)
    assert row.idle_seconds == Decimal(0)
    assert row.offline_seconds == Decimal(0)
    assert row.sample_count == 598


async def test_a_span_across_the_hour_is_split_and_not_counted_twice(
    db_session: AsyncSession,
) -> None:
    """No sample lands on :00, so the hour is only whole if the carry-in works.

    Polls at :57 and :02 past every five seconds mean the 10:00 bucket opens two
    seconds before its first sample of its own and closes three seconds after its
    last. Those five seconds belong to the sample either side, split at the
    boundary — counted once each, in different buckets.
    """
    printer = new_id()
    # 09:30:02 → 11:00:02, offset so nothing coincides with an hour boundary.
    start = PREVIOUS_HOUR + timedelta(minutes=30, seconds=2)
    await record(db_session, printer, first=start, count=1081)

    await summarise(db_session)

    partial = await bucket(db_session, printer, PREVIOUS_HOUR)
    whole = await bucket(db_session, printer, HOUR)
    # 09:30:02 → 10:00:00 is 1798 seconds, and nothing before it was observed.
    assert partial.observed_seconds == Decimal(1798)
    assert whole.observed_seconds == Decimal(3600)
    assert whole.sample_count == 720


async def test_each_printer_gets_its_own_row(db_session: AsyncSession) -> None:
    """One grain, one uniqueness rule — no farm-wide total row to double-count."""
    first, second = new_id(), new_id()
    await a_full_hour(db_session, first)
    await a_full_hour(db_session, second, state=PrinterState.IDLE)

    await summarise(db_session)

    rows = list(
        await db_session.scalars(select(MetricRollup).where(MetricRollup.bucket_start == HOUR))
    )
    assert {row.printer_id for row in rows} == {first, second}
    assert len(rows) == 2


# ------------------------------------------------------------ readings kept honest


async def test_an_hour_with_no_temperature_readings_is_null_not_zero(
    db_session: AsyncSession,
) -> None:
    """`sample_of` refuses to invent a cold bed; the summary must not undo it."""
    printer = new_id()
    await a_full_hour(db_session, printer)

    await summarise(db_session)

    row = await bucket(db_session, printer, HOUR)
    assert row.nozzle_temp_avg_c is None
    assert row.nozzle_temp_max_c is None
    assert row.bed_temp_avg_c is None
    assert row.bed_temp_max_c is None


async def test_temperatures_are_averaged_and_peaked(db_session: AsyncSession) -> None:
    printer = new_id()
    await record(
        db_session,
        printer,
        first=HOUR,
        count=360,
        nozzle_temp_c=Decimal(200),
        bed_temp_c=Decimal(60),
    )
    await record(
        db_session,
        printer,
        first=HOUR + timedelta(minutes=30),
        count=361,
        nozzle_temp_c=Decimal(240),
        bed_temp_c=Decimal(60),
    )

    await summarise(db_session)

    row = await bucket(db_session, printer, HOUR)
    assert row.nozzle_temp_avg_c == Decimal(220)
    assert row.nozzle_temp_max_c == Decimal(240)
    assert row.bed_temp_max_c == Decimal(60)


async def test_an_error_code_while_printing_is_not_time_spent_in_error(
    db_session: AsyncSession,
) -> None:
    """The reason `error_seconds` and `error_sample_count` are both kept.

    A machine can raise a code and keep printing. Reading either number as the
    other turns a warning into an outage on the dashboard, or hides an outage
    behind a warning.
    """
    printer = new_id()
    await a_full_hour(db_session, printer, error_code="HMS_0300_0100")

    await summarise(db_session)

    row = await bucket(db_session, printer, HOUR)
    assert row.printing_seconds == Decimal(3600)
    assert row.error_seconds == Decimal(0)
    assert row.error_sample_count == 720
    assert row.error_codes == {"HMS_0300_0100": 720}
