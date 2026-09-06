"""That the failure sweep the worker actually runs is wired to anything at all.

`test_service_sweep.py` drives a `ServiceSweep` this file's neighbour assembled by
hand, which is the right shape for asserting *behaviour* and the wrong shape for
asserting *wiring*. Delete `ServicePass` from `workers/passes.py`, or its loop from
`runner.py`, and every behaviour test above stays green while the farm records no
failure ever again — silently, which is the failure mode HANDOFF records
`test_intake_pass_wiring.py` as the only existing cover for.

So this goes through `ServicePass` itself, on that file's model. The runtime is a
stand-in rather than a real `WorkerRuntime` because the real one opens Redis for the
heartbeat, the relay and the driver states, and a wiring test needing three network
services is a wiring test nobody runs. What it supplies is exactly what the pass
reads: the session it must work in, the clock the desk is built with, and the
settings the beat's window comes from.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.service import FailureOrigin
from printorian.contexts.service.models import PrinterFailure
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.drivers import PrinterState
from printorian.workers.passes import ServicePass
from tests.unit._measure_support import HOUR
from tests.unit._service_support import a_printer


class _Runtime:
    """As much of `WorkerRuntime` as `ServicePass` touches, and nothing else.

    The beat is recorded rather than sent: `record_beat` is the one call that would
    reach Redis, and whether it fired is worth pinning here too — a pass that does
    its work and never reports it is a pass `/health/workers` calls wedged, which
    since this loop was added is a thing it can now say by name.
    """

    def __init__(self, session: AsyncSession, clock: FixedClock, settings: Settings) -> None:
        self._session = session
        self.clock = clock
        self.settings = settings
        self.beats: list[tuple[str, int]] = []

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        # Yielded without closing or committing: the test owns this session and
        # rolls it back, which is what keeps the assertions afterwards readable.
        yield self._session

    async def record_beat(self, loop: str, interval_seconds: int) -> None:
        self.beats.append((loop, interval_seconds))


@pytest.fixture
def runtime(db_session: AsyncSession, clock: FixedClock, settings: Settings) -> _Runtime:
    return _Runtime(db_session, clock, settings)


async def test_the_service_sweep_is_actually_wired_into_the_worker(
    db_session: AsyncSession, runtime: _Runtime
) -> None:
    """A broken machine becomes a failure record through the pass the worker runs.

    Not through a sweep this test assembled. Remove `ServiceSweep(...)` from
    `ServicePass.sweep` and this is the only assertion in the suite that fails.
    """
    printer = await a_printer(
        db_session,
        state=PrinterState.ERROR,
        last_seen_at=HOUR,
        error_code="bambu.print_error.0300_8003",
    )

    outcome = await ServicePass(runtime).sweep()  # type: ignore[arg-type]

    assert outcome.opened == 1
    failure = await db_session.scalar(
        select(PrinterFailure).where(PrinterFailure.printer_id == printer)
    )
    assert failure is not None
    assert failure.origin is FailureOrigin.DRIVER
    assert runtime.beats == [("service", runtime.settings.service_sweep_seconds)]
