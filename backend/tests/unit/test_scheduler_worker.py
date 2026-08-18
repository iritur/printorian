"""The scheduler tick.

Two things must hold. A pass plans *and* sends, so a paid repeat order reaches a
machine with nobody touching it. And the loop wakes on an event rather than only
on the timer — a printer that finishes a second after a tick must not stand idle
for the rest of the interval with work in the queue.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import ConnectionMode, CreatePrinter, FleetService
from printorian.contexts.production import CreateJob, JobStatus, ProductionService
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.secrets import SecretBox
from printorian.core.storage import InMemoryObjectStore
from printorian.drivers import JobHandle, PrinterState, RemoteFileRef, Telemetry
from printorian.workers.scheduler import (
    REPLAN_TRIGGERS,
    SchedulerTick,
    attach_replanning,
    run_forever,
)
from tests.conftest import ensure_order

#: One order, reused by every job this module builds.
#:
#: PostgreSQL enforces the foreign key, so a job needs an order that exists. A
#: module constant rather than a fresh id per call: the tests here are about
#: scheduling and dispatch, and which order a job belongs to is not a variable any
#: of them vary.
SEED_ORDER_ID = new_id()


@pytest.fixture(autouse=True)
async def _the_order_exists(db_session: AsyncSession) -> None:
    await ensure_order(db_session, SEED_ORDER_ID)


KEY = "a-development-secret-key-not-for-production"


@pytest.fixture
def fleet(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> FleetService:
    return FleetService(db_session, clock, bus, SecretBox(KEY))


@pytest.fixture
def production(
    db_session: AsyncSession,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> ProductionService:
    return ProductionService(db_session, clock, bus, object_store)


class _Driver:
    def __init__(self) -> None:
        self.started = False

    async def upload(self, plate: object) -> RemoteFileRef:
        return RemoteFileRef(path="/cache/plate.3mf")

    async def start(self, ref: RemoteFileRef, ams_mapping: dict[int, int]) -> JobHandle:
        self.started = True
        return JobHandle(value="handle-1")


def a_job(**overrides: object) -> CreateJob:
    base: dict[str, object] = {
        "order_id": SEED_ORDER_ID,
        "material_type": "PLA",
        # Hex, because that is what the fleet reports for an AMS slot. See the
        # note in `test_a_colour_vocabulary_mismatch_waits_rather_than_misprints`.
        "colors": ["#FFFFFF"],
        "width_mm": Decimal(40),
        "depth_mm": Decimal(40),
        "height_mm": Decimal(40),
        "grams_required": Decimal(20),
        "estimated_minutes": Decimal(60),
    }
    return CreateJob(**{**base, **overrides})  # type: ignore[arg-type]


async def a_loaded_printer(fleet: FleetService, clock: FixedClock, name: str = "p1"):
    """A registered machine that has reported itself idle with PLA loaded."""
    from printorian.drivers import AmsSlot

    view = await fleet.register(
        CreatePrinter(
            name=name,
            brand="bambu",
            serial=f"SN-{name}",
            connection_mode=ConnectionMode.LAN,
            host="192.168.0.10",
            access_code="12345678",
            acquisition_cost=Decimal(100_000),
            expected_lifetime_hours=20_000,
        )
    )
    await fleet.record(
        view.id,
        Telemetry(
            printer_id=str(view.id),
            observed_at=clock.now(),
            state=PrinterState.IDLE,
            ams_slots=(
                AmsSlot(
                    unit=0, index=0, material_type="PLA", colour_hex="#FFFFFF", remaining_percent=90
                ),
            ),
        ),
    )
    return view


# ------------------------------------------------------------- one pass


async def test_a_tick_plans_and_dispatches_in_one_pass(
    production: ProductionService,
    fleet: FleetService,
    clock: FixedClock,
    with_plate: Callable[..., Awaitable[None]],
) -> None:
    """Phase 4's exit criterion: from ready work to a machine printing, with
    nobody in between."""
    printer = await a_loaded_printer(fleet, clock)
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)

    driver = _Driver()
    tick = SchedulerTick(production, fleet, {str(printer.id): driver})  # type: ignore[dict-item]
    outcome = await tick.tick()

    assert outcome.assigned == 1
    assert outcome.dispatched == 1
    assert driver.started
    assert (await production.get(job.id)).status is JobStatus.PRINTING


async def test_a_tick_over_an_empty_queue_does_nothing_quietly(
    production: ProductionService, fleet: FleetService, clock: FixedClock
) -> None:
    await a_loaded_printer(fleet, clock)

    outcome = await SchedulerTick(production, fleet, {}).tick()

    assert outcome == type(outcome)()


async def test_a_job_with_no_driver_returns_to_the_queue(
    production: ProductionService, fleet: FleetService, clock: FixedClock
) -> None:
    """ADR-0007 again, at the worker level: a manual machine is driven by a
    person, and the pass says so rather than counting a dispatch."""
    await a_loaded_printer(fleet, clock)
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")

    outcome = await SchedulerTick(production, fleet, {}).tick()

    assert outcome.assigned == 1
    assert outcome.dispatched == 0
    assert outcome.dispatch_failed == 1
    assert (await production.get(job.id)).status is JobStatus.READY


async def test_the_planner_sees_what_each_machine_already_holds(
    production: ProductionService, fleet: FleetService, clock: FixedClock
) -> None:
    """Queue depth is what stops everything piling onto one printer. Leaving out
    the running job would make a busy machine look as free as an idle one."""
    printer = await a_loaded_printer(fleet, clock)
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await production.plan_pass(await SchedulerTick(production, fleet, {}).schedulable_printers())

    printers = await SchedulerTick(production, fleet, {}).schedulable_printers()
    mine = next(p for p in printers if p.printer_id == str(printer.id))

    assert mine.queued_minutes == Decimal(60)


async def test_amortization_reaches_the_planner(
    production: ProductionService, fleet: FleetService, clock: FixedClock
) -> None:
    printer = await a_loaded_printer(fleet, clock)

    printers = await SchedulerTick(production, fleet, {}).schedulable_printers()
    mine = next(p for p in printers if p.printer_id == str(printer.id))

    # 100_000 over 20_000 hours.
    assert mine.amortization_per_hour == Decimal("5.00")


# ------------------------------------------------------ event-driven wake


def test_the_triggers_cover_what_changes_the_answer() -> None:
    """ARCHITECTURE §6 names these. A machine coming free is the one that costs
    real capacity if it is missed."""
    assert "job.ready" in REPLAN_TRIGGERS
    assert "printer.became_free" in REPLAN_TRIGGERS


async def test_a_relevant_event_wakes_the_scheduler(bus: EventBus) -> None:
    from printorian.contexts.production import events as job_events

    wake = asyncio.Event()
    attach_replanning(bus, wake)

    await bus.publish(job_events.PrinterBecameFree(printer_id=new_id()))

    assert wake.is_set()


async def test_an_unrelated_event_does_not(bus: EventBus) -> None:
    """Waking on everything would turn the interval into a busy loop."""
    from printorian.contexts.payments import events as payment_events

    wake = asyncio.Event()
    attach_replanning(bus, wake)

    await bus.publish(
        payment_events.PaymentSettled(payment_id=new_id(), order_id=new_id(), amount="100")
    )

    assert not wake.is_set()


async def test_the_loop_runs_a_pass_and_stops_when_asked() -> None:
    passes = 0

    class _Tick:
        async def tick(self):
            nonlocal passes
            passes += 1
            from printorian.workers.scheduler import TickOutcome

            return TickOutcome()

    async def build():
        return _Tick()

    stop = asyncio.Event()
    task = asyncio.create_task(run_forever(build, interval_seconds=60, stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert passes >= 1


async def test_a_wake_runs_another_pass_without_waiting_for_the_interval() -> None:
    """The whole point: a printer finishing must not wait out a 30-second tick."""
    passes = 0

    class _Tick:
        async def tick(self):
            nonlocal passes
            passes += 1
            from printorian.workers.scheduler import TickOutcome

            return TickOutcome()

    async def build():
        return _Tick()

    wake = asyncio.Event()
    stop = asyncio.Event()
    # An interval long enough that a second pass can only come from the wake.
    task = asyncio.create_task(run_forever(build, interval_seconds=3600, wake=wake, stop=stop))
    await asyncio.sleep(0.05)
    first = passes

    wake.set()
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert passes > first


async def test_a_failing_pass_does_not_end_the_loop() -> None:
    """One bad pass must not silently stop the farm from ever scheduling again."""
    attempts = 0

    class _Tick:
        async def tick(self):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("database went away")

    async def build():
        return _Tick()

    wake = asyncio.Event()
    stop = asyncio.Event()
    task = asyncio.create_task(run_forever(build, interval_seconds=3600, wake=wake, stop=stop))
    await asyncio.sleep(0.05)
    wake.set()
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert attempts >= 2


async def test_a_colour_vocabulary_mismatch_waits_rather_than_misprints(
    production: ProductionService, fleet: FleetService, clock: FixedClock
) -> None:
    """A job whose colours are named differently from the AMS slot is refused.

    The fleet reports slot colours as hex, from the machine. An order carrying a
    name — or the configurator's current `colour-1` placeholder — matches nothing,
    and the job wait-lists as "material not loaded" instead of being sent to a
    printer holding the wrong filament.

    That is the safe failure, not the right one: the two vocabularies still have
    to be reconciled before the colour pipeline is finished. This test pins the
    behaviour so the reconciliation is a deliberate change rather than a surprise.
    """
    await a_loaded_printer(fleet, clock)
    job = await production.create_job(a_job(colors=["white"]))
    await production.mark_ready(job.id, plate_filename="cube.3mf")

    outcome = await SchedulerTick(production, fleet, {}).tick()

    assert outcome.assigned == 0
    assert outcome.wait_listed == 1
    entry = (await production.wait_list())[0]
    assert "reject.colour_not_loaded" in entry.blocking_reasons
