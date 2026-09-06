"""The world a failure test needs: machines, a desk, and a window over both.

Printer rows are written straight rather than registered through `FleetService`,
for the reason `_measure_support` gives about `metric_rollups`: these tests are
about what a *reader* does with rows that exist, and routing every one of them
through the registry would make an assertion about reliability fail whenever
registration changed. The one thing that must be real is the row's identity — the
foreign keys on `printer_failures` are enforced (ADR-0021, and `tests/factories`
exists because they are).

The window is `_measure_support`'s, so failures and summarised hours are always
counted over the same two hours; a suite with two windows would let a numerator and
a denominator drift apart and still look green.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import Grain, MetricWindow
from printorian.contexts.fleet.models import Printer
from printorian.contexts.service import FailureCause, FailureOrigin, ServiceDesk
from printorian.core.ids import EntityId, new_id
from printorian.drivers import PrinterState
from tests.unit._measure_support import HOUR

#: The window every test here reads over: the two closed hours `_measure_support`
#: writes rollups into.
WINDOW = MetricWindow(since=HOUR, until=HOUR + timedelta(hours=2), grain=Grain.TOTAL)


async def a_printer(
    db: AsyncSession,
    *,
    name: str = "P-01",
    state: PrinterState = PrinterState.IDLE,
    last_seen_at: datetime | None = None,
    error_code: str | None = None,
    is_active: bool = True,
) -> EntityId:
    """One registered machine, in a state and with a last observation of its own."""
    printer = Printer(
        id=new_id(),
        name=name,
        state=state,
        last_seen_at=last_seen_at,
        last_telemetry={"error_code": error_code} if error_code is not None else {},
        is_active=is_active,
    )
    db.add(printer)
    await db.flush()
    return printer.id


async def a_failure(
    desk: ServiceDesk,
    printer_id: EntityId,
    *,
    at: datetime = HOUR,
    restored_at: datetime | None = None,
    cause: FailureCause | None = None,
    origin: FailureOrigin = FailureOrigin.PERSON,
    error_code: str | None = None,
) -> EntityId:
    """One failure, optionally already repaired.

    `restored_at` goes through `ServiceDesk.restore` rather than onto the row, so a
    test that sets up a *closed* failure exercises the same refusals a real close
    does — a fixture that wrote the column directly could set up a state the
    application cannot reach.
    """
    failure = await desk.record(
        printer_id, origin, cause=cause, error_code=error_code, detected_at=at
    )
    if restored_at is not None:
        await desk.restore(failure.id, restored_at)
    return failure.id


__all__ = ["WINDOW", "a_failure", "a_printer"]
