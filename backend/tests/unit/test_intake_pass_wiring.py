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

The other half is now the postprocessing catalogue, and it needs a different kind
of assertion than the plate library does. A dropped plate library shows up as an
order landing in `PREP`; a dropped catalogue shows up as *nothing at all*, because
`prepared_cost` is a difference and the finish term is identical on both sides of
it, so it cancels exactly. That cancellation is a property of `pricing.reprice`,
not a promise `CachedPlates` may lean on — the sweep is supposed to reprice from
the same rows the checkout charged from — so the wiring is asserted directly,
against the argument the pass hands over.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary
from printorian.contexts.ordering import OrderStatus
from printorian.contexts.pricing import FinishOption
from printorian.contexts.production import JobStatus
from printorian.contexts.settings import SettingsService
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.workers import passes as passes_module
from printorian.workers.cached_plates import CachedPlates
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


async def test_the_intake_pass_reprices_from_the_farms_own_finish_catalogue(
    db_session: AsyncSession,
    library: PlateLibrary,
    runtime: _Runtime,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep gets the resolved catalogue, not the code constant.

    Asserted on the argument rather than on the money, and that is deliberate
    rather than lazy: `prepared_cost` is a difference between two prices that share
    their finishes, so the finish term cancels exactly and no total in this sweep
    moves when the catalogue does. There is therefore no observable figure to
    assert — which is precisely the shape of hole HANDOFF records for `cached=`,
    where deleting an argument left the whole suite green.

    Dropping `finishes=` from the `CachedPlates(...)` call would now raise
    `TypeError` and fail every test that runs the pass; passing
    `FINISH_CATALOGUE` there instead of resolving would fail only this one.
    """
    await SettingsService(db_session, clock).set_value(
        "postprocess.operations",
        [
            {"code": "raw", "labor_hours": "0", "flat_fee": "0", "extra_days": 0},
            {"code": "sanded", "labor_hours": "0.9", "flat_fee": "0", "extra_days": 0},
            {"code": "primed", "labor_hours": "0.6", "flat_fee": "150", "extra_days": 0},
            {"code": "painted", "labor_hours": "1.5", "flat_fee": "400", "extra_days": 2},
        ],
        by=None,
    )
    await db_session.flush()

    handed: dict[str, FinishOption] = {}

    class _Capturing(CachedPlates):
        def __init__(
            self, db: AsyncSession, plates: PlateLibrary, *, finishes: Mapping[str, FinishOption]
        ) -> None:
            handed.update(finishes)
            super().__init__(db, plates, finishes=finishes)

    monkeypatch.setattr(passes_module, "CachedPlates", _Capturing)

    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    await a_paid_order(db_session, number="WIRED-2", asset_id=asset_id)

    await IntakePass(runtime).sweep()  # type: ignore[arg-type]

    assert handed["sanded"].labor_hours == Decimal("0.9")
