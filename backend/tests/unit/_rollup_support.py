"""Fixtures for the rollup tests, shared by the two files that split off.

Writing telemetry by hand is most of the work in these tests, and the exact
shape of a written sample — `created_at` set explicitly, `observed_at`
deliberately skewed from it — is the part that has to stay identical across
both files. One copy, so the two cannot drift into testing different things.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import rollups
from printorian.contexts.fleet.history import MetricRollup, TelemetrySample
from printorian.core.ids import EntityId, new_id
from printorian.drivers import PrinterState

#: The hour under test, and an hour before it to carry state in from.
HOUR = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
PREVIOUS_HOUR = HOUR - timedelta(hours=1)
#: Two hours after `HOUR` starts, so `HOUR` and the one after it are both *closed*.
NOW = HOUR + timedelta(hours=2)

#: `telemetry_poll_seconds` (5) × `rollup_gap_intervals` (3), the shipped default.
GAP_SECONDS = 15
POLL = timedelta(seconds=5)


async def record(
    db: AsyncSession,
    printer_id: EntityId,
    *,
    first: datetime,
    count: int,
    state: PrinterState = PrinterState.PRINTING,
    step: timedelta = POLL,
    nozzle_temp_c: Decimal | None = None,
    bed_temp_c: Decimal | None = None,
    error_code: str | None = None,
) -> datetime:
    """Write ``count`` polls, and return the moment after the last one.

    `created_at` is set explicitly rather than left to the server default: it is
    the column the rollup buckets on, and a test that let the database stamp it
    would be summarising whenever the suite happened to run.
    """
    rows = [
        {
            "id": new_id(),
            "created_at": first + step * index,
            "printer_id": printer_id,
            # Deliberately *not* equal to `created_at`: the machine's clock and the
            # farm's differ by the poll's round trip, and the rollup must be
            # indifferent to it.
            "observed_at": first + step * index - timedelta(milliseconds=800),
            "state": state,
            "nozzle_temp_c": nozzle_temp_c,
            "bed_temp_c": bed_temp_c,
            "error_code": error_code,
        }
        for index in range(count)
    ]
    await db.execute(insert(TelemetrySample), rows)
    return first + step * count


async def summarise(db: AsyncSession, *, now: datetime = NOW, since: datetime | None = None):
    return await rollups.summarise(
        db, now=now, gap_seconds=GAP_SECONDS, max_buckets=24, since=since
    )


async def bucket(db: AsyncSession, printer_id: EntityId, start: datetime) -> MetricRollup:
    row = await db.scalar(
        select(MetricRollup).where(
            MetricRollup.printer_id == printer_id, MetricRollup.bucket_start == start
        )
    )
    assert row is not None, f"no rollup for {start.isoformat()}"
    return row


async def a_full_hour(db: AsyncSession, printer_id: EntityId, **fields) -> None:
    """720 polls filling `HOUR`, plus the one at 11:00 that closes the last span."""
    await record(db, printer_id, first=HOUR, count=720, **fields)
    await record(db, printer_id, first=HOUR + timedelta(hours=1), count=1, **fields)
