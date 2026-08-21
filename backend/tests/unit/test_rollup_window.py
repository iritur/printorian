"""Which hours a sweep takes, and what happens when it takes one twice.

The arithmetic is `test_rollups.py`. This is the half that decides whether the
sweep is *safe to run on a timer*: it must never write the hour still in progress,
must stop after a bounded amount of work, must advance its watermark, and must be
harmless to repeat — because retention clamps its cutoff to that watermark, and a
sweep that lies about how far it has reached is how raw samples get dropped before
anything summarised them.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import rollups
from printorian.contexts.fleet.history import MetricRollup, TelemetrySample
from printorian.core.ids import new_id
from printorian.drivers import PrinterState
from tests.unit._rollup_support import (
    GAP_SECONDS,
    HOUR,
    PREVIOUS_HOUR,
    a_full_hour,
    bucket,
    record,
    summarise,
)

# ------------------------------------------------------------ the window


async def test_the_incomplete_current_hour_is_never_written(db_session: AsyncSession) -> None:
    """A partial bucket nothing rewrites is a permanently wrong row.

    The live hour is served from raw samples, which the retention window keeps, so
    there is nothing to gain by guessing at it half an hour early.
    """
    printer = new_id()
    await record(db_session, printer, first=PREVIOUS_HOUR, count=720)
    await record(db_session, printer, first=HOUR, count=360)

    outcome = await summarise(db_session, now=HOUR + timedelta(minutes=30))

    written = list(await db_session.scalars(select(MetricRollup.bucket_start)))
    assert written == [PREVIOUS_HOUR]
    assert outcome.window_end == HOUR


async def test_the_sweep_stops_at_max_buckets(db_session: AsyncSession) -> None:
    """Catch-up is bounded, because `db_statement_timeout_ms` is 30 seconds."""
    printer = new_id()
    for hour in range(5):
        await record(db_session, printer, first=HOUR + timedelta(hours=hour), count=720)

    outcome = await rollups.summarise(
        db_session, now=HOUR + timedelta(hours=5), gap_seconds=GAP_SECONDS, max_buckets=2
    )

    assert outcome.buckets == 2
    assert outcome.window_end == HOUR + timedelta(hours=2)
    assert len(list(await db_session.scalars(select(MetricRollup.bucket_start)))) == 2


async def test_the_watermark_advances_and_the_next_sweep_does_nothing(
    db_session: AsyncSession,
) -> None:
    printer = new_id()
    await a_full_hour(db_session, printer)

    first = await summarise(db_session)
    second = await summarise(db_session)

    assert first.rows_written > 0
    assert await rollups.latest_bucket(db_session) == HOUR + timedelta(hours=1)
    assert second.rows_written == 0
    assert second.window_start is None


async def test_a_stretch_with_no_samples_does_not_wedge_the_sweep(
    db_session: AsyncSession,
) -> None:
    """Two days of silence must not stop the rollups resuming afterwards.

    Without the skip-ahead the watermark can only move by writing a row, so a
    window holding nothing at all leaves the sweep re-scanning the same dead hours
    for ever and never reaching the samples that arrived after them.
    """
    printer = new_id()
    await a_full_hour(db_session, printer)
    later = HOUR + timedelta(days=3)
    await record(db_session, printer, first=later, count=720)
    now = later + timedelta(hours=2)

    await rollups.summarise(db_session, now=now, gap_seconds=GAP_SECONDS, max_buckets=24)
    resumed = await rollups.summarise(db_session, now=now, gap_seconds=GAP_SECONDS, max_buckets=24)

    assert resumed.window_start == later
    assert (await bucket(db_session, printer, later)).sample_count == 720


# ------------------------------------------------------------ idempotency


async def test_running_the_same_window_twice_changes_nothing(db_session: AsyncSession) -> None:
    """The requirement in one test: a re-run must not double count."""
    printer = new_id()
    await a_full_hour(db_session, printer)
    await summarise(db_session)
    before = (await bucket(db_session, printer, HOUR)).observed_seconds

    await summarise(db_session, since=HOUR)

    rows = list(
        await db_session.scalars(select(MetricRollup).where(MetricRollup.bucket_start == HOUR))
    )
    assert len(rows) == 1
    assert rows[0].observed_seconds == before == Decimal(3600)
    assert rows[0].printing_seconds == Decimal(3600)


async def test_a_corrected_sample_corrects_the_row(db_session: AsyncSession) -> None:
    """`DO UPDATE`, not `DO NOTHING` — which would leave a wrong row wrong.

    Recompute-and-overwrite makes the row a pure function of the samples in its
    window, so repairing history is a matter of repairing the samples and sweeping
    again rather than of finding and hand-editing a summary.
    """
    printer = new_id()
    await a_full_hour(db_session, printer)
    await summarise(db_session)

    await db_session.execute(
        update(TelemetrySample)
        .where(TelemetrySample.created_at >= HOUR + timedelta(minutes=30))
        .where(TelemetrySample.created_at < HOUR + timedelta(hours=1))
        .values(state=PrinterState.ERROR)
    )
    await summarise(db_session, since=HOUR)

    row = await bucket(db_session, printer, HOUR)
    assert row.printing_seconds == Decimal(1800)
    assert row.error_seconds == Decimal(1800)
    assert await db_session.scalar(text("SELECT count(*) FROM metric_rollups")) == 2
