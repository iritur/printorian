"""Rows the measure tests read back, written straight into `metric_rollups`.

Deliberately *not* routed through `rollups.summarise`. That function is tested on
its own in `test_rollups.py`, and going through it here would make every assertion
about reading depend on the arithmetic of writing — so a change to the attribution
rule would fail two suites and only one of them would be about the change. These
tests are about what a reader does with a row that exists and with an hour that has
no row at all, which is easiest to state by putting exactly those rows there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.history import MetricRollup
from printorian.core.ids import EntityId

#: The first hour every window in these tests opens on, and a "now" two hours past
#: it so that hour and the one after it are both closed.
HOUR = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
NOW = HOUR + timedelta(hours=2)

#: An hour whose every second was accounted for.
FULL_HOUR = Decimal(3600)


async def summarised(
    db: AsyncSession,
    printer_id: EntityId,
    bucket_start: datetime,
    *,
    observed_seconds: Decimal = FULL_HOUR,
    printing_seconds: Decimal = Decimal(0),
    idle_seconds: Decimal = Decimal(0),
    offline_seconds: Decimal = Decimal(0),
    error_seconds: Decimal = Decimal(0),
    sample_count: int = 720,
    state_changes: int = 0,
    error_sample_count: int = 0,
    error_codes: dict[str, int] | None = None,
    nozzle_temp_avg_c: Decimal | None = None,
    nozzle_temp_max_c: Decimal | None = None,
    bed_temp_avg_c: Decimal | None = None,
    bed_temp_max_c: Decimal | None = None,
) -> None:
    """One summarised hour for one machine.

    The four states not in the signature stay zero — they are zero on most rows and
    naming all eight at every call site would bury the one the test is about.
    """
    db.add(
        MetricRollup(
            printer_id=printer_id,
            bucket_start=bucket_start,
            sample_count=sample_count,
            observed_seconds=observed_seconds,
            offline_seconds=offline_seconds,
            idle_seconds=idle_seconds,
            preparing_seconds=Decimal(0),
            printing_seconds=printing_seconds,
            paused_seconds=Decimal(0),
            finished_seconds=Decimal(0),
            error_seconds=error_seconds,
            maintenance_seconds=Decimal(0),
            state_changes=state_changes,
            error_sample_count=error_sample_count,
            error_codes=error_codes or {},
            nozzle_temp_avg_c=nozzle_temp_avg_c,
            nozzle_temp_max_c=nozzle_temp_max_c,
            bed_temp_avg_c=bed_temp_avg_c,
            bed_temp_max_c=bed_temp_max_c,
        )
    )
    await db.flush()


__all__ = ["FULL_HOUR", "HOUR", "NOW", "summarised"]
