"""That the pass the worker actually runs is wired to close ADR-0006's loop.

Everything else about the cache-hit path is tested through a sweep the test
builds itself (`tests/unit/_intake_cache_support.a_sweep`), which is the right
shape for asserting *behaviour* and the wrong shape for asserting *wiring*:
`workers/passes.py` supplies `CachedPlates` and ADR-0013's band to `IntakeSweep`,
and deleting that one argument turns the whole of
[#58](https://github.com/iritur/printorian/issues/58) off. Every order goes to
prep, exactly as it did before — no exception, no log line, and
`test_a_sweep_with_no_plate_library_behaves_as_it_did_before` asserts precisely
that this fallback is *safe*, which is the opposite assertion to the one needed
here.

So this file goes through `IntakePass` itself. The runtime is a stand-in rather
than a real `WorkerRuntime` because the real one opens Redis for the heartbeat,
the relay and the driver states, and a wiring test that needs three network
services to run is a wiring test nobody runs. What it does supply is what the pass
reads: the session it must work in, the clock, the bus, and the settings the
tolerance comes from — because "the band is configuration and never a constant"
is half of what this asserts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary
from printorian.contexts.ordering import OrderStatus
from printorian.contexts.production import JobStatus
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.workers.passes import IntakePass
from tests.unit._intake_cache_support import (
    a_cached_plate,
    a_material,
    a_paid_order,
    an_asset,
    status_of,
    the_job,
)


class _Runtime:
    """As much of `WorkerRuntime` as `IntakePass` touches, and nothing else.

    The beat is recorded rather than sent: `record_beat` is the one call that would
    reach Redis, and whether it fired is worth pinning here too — a pass that does
    its work and never reports it is a pass the health check calls wedged.
    """

    def __init__(
        self, session: AsyncSession, clock: FixedClock, bus: EventBus, settings: Settings
    ) -> None:
        self._session = session
        self.clock = clock
        self.bus = bus
        self.settings = settings
        self.beats: list[tuple[str, int]] = []

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        # Yielded without closing or committing: the test owns this session and
        # rolls it back, which is what keeps the assertions readable afterwards.
        yield self._session

    async def record_beat(self, loop: str, interval_seconds: int) -> None:
        self.beats.append((loop, interval_seconds))


@pytest.fixture
def library(db_session: AsyncSession, clock: FixedClock) -> PlateLibrary:
    return PlateLibrary(db_session, clock)


@pytest.fixture
def runtime(
    db_session: AsyncSession, clock: FixedClock, bus: EventBus, settings: Settings
) -> _Runtime:
    return _Runtime(db_session, clock, bus, settings)


async def test_the_intake_pass_is_wired_to_the_plate_library(
    db_session: AsyncSession, library: PlateLibrary, runtime: _Runtime
) -> None:
    """A paid cache-hit order reaches `QUEUED` through the pass the worker runs.

    Not through a sweep this test assembled. Remove `cached` from the
    `IntakeSweep(...)` call in `workers/passes.py` and this order lands in `PREP`
    with a `PENDING` job — which is the silent return to clicking that no other
    test in the suite notices.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="WIRED-1", asset_id=asset_id)

    outcome = await IntakePass(runtime).sweep()  # type: ignore[arg-type]

    assert outcome.raised == 1
    assert (await the_job(db_session, order_id)).status is JobStatus.READY
    assert await status_of(db_session, order_id) is OrderStatus.QUEUED
    assert runtime.beats == [("intake", runtime.settings.intake_sweep_seconds)]
