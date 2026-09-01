"""What a customer is told about where their work stands (C7).

The distinction that matters: a job queueing for a machine gets a place and a
time; a job blocked on a person gets a reason and neither. Numbering the second
kind would promise movement that is not coming.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import PrinterCapability
from printorian.contexts.production import (
    CreateJob,
    JobStatus,
    JobView,
    ProductionService,
    WaitListEntry,
    wait_list_size,
)
from printorian.contexts.scheduling import SchedulablePrinter
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.drivers import PrinterState
from tests.conftest import ensure_order, ensure_plate, ensure_printer

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


#: One prepared plate, for the jobs this module attaches one to.
SEED_PLATE_ID = new_id()


@pytest.fixture(autouse=True)
async def _the_plate_exists(db_session: AsyncSession) -> None:
    await ensure_plate(db_session, SEED_PLATE_ID)


TOLERANCE = Decimal("0.15")


@pytest.fixture
def production(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> ProductionService:
    return ProductionService(db_session, clock, bus)


def a_job(**overrides: object) -> CreateJob:
    base: dict[str, object] = {
        "order_id": SEED_ORDER_ID,
        "material_type": "PLA",
        "colors": ["white"],
        "width_mm": Decimal(100),
        "depth_mm": Decimal(100),
        "height_mm": Decimal(100),
        "grams_required": Decimal(50),
        "estimated_minutes": Decimal(120),
    }
    return CreateJob(**{**base, **overrides})  # type: ignore[arg-type]


def a_printer(
    *,
    loaded: tuple = (("PLA", "white", Decimal(800)),),
    state: PrinterState = PrinterState.IDLE,
    free_at=None,
    printer_id=None,
    width_mm: Decimal = Decimal(256),
) -> SchedulablePrinter:
    # `printer_id` is a parameter because `print_jobs.printer_id` is a real foreign
    # key: a test that expects an *assignment* has to plan onto a machine the fleet
    # actually holds, which the caller has to have created (`ensure_printer`).
    return SchedulablePrinter(
        capability=PrinterCapability(
            printer_id=str(printer_id or new_id()),
            state=state,
            width_mm=width_mm,
            depth_mm=Decimal(256),
            height_mm=Decimal(256),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=False,
            loaded=loaded,
        ),
        free_at=free_at,
    )


async def _prepared(production: ProductionService, **overrides: object) -> JobView:
    """A job with its plate attached, which is what makes the planner look at it."""
    job = await production.create_job(a_job(**overrides))
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(60),
        total_grams=Decimal(20),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1000),
        tolerance=TOLERANCE,
    )
    return job


async def test_an_order_with_no_job_has_no_queue_position(
    production: ProductionService,
) -> None:
    """Paid but nothing created yet is a real state, not an error."""
    assert await production.queue_position(new_id()) is None


async def test_a_job_that_is_not_waiting_reports_only_its_status(
    production: ProductionService,
) -> None:
    job = await production.create_job(a_job())

    position = await production.queue_position(job.order_id)

    assert position is not None
    assert position.job_status is JobStatus.PENDING
    assert position.position is None
    assert position.predicted_start is None


async def test_a_job_waiting_on_a_person_gets_a_reason_and_no_number(
    production: ProductionService,
) -> None:
    """Numbering it "3rd" would promise movement that is not coming — nothing
    knows when somebody will mount a spool."""
    from printorian.contexts.fleet import PrinterCapability
    from printorian.contexts.scheduling import SchedulablePrinter
    from printorian.drivers import PrinterState

    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(60),
        total_grams=Decimal(20),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1000),
        tolerance=TOLERANCE,
    )
    wrong_material = SchedulablePrinter(
        capability=PrinterCapability(
            printer_id=str(new_id()),
            state=PrinterState.IDLE,
            width_mm=Decimal(256),
            depth_mm=Decimal(256),
            height_mm=Decimal(256),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=False,
            loaded=(("PETG", "white", Decimal(800)),),
        )
    )
    await production.plan_pass([wrong_material])

    position = await production.queue_position(job.order_id)

    assert position is not None
    assert position.reason == "waitlist.material_not_loaded"
    assert position.position is None
    assert position.predicted_start is None


async def test_a_job_queueing_for_capacity_gets_a_place_and_a_time(
    production: ProductionService, clock: FixedClock
) -> None:
    from printorian.contexts.fleet import PrinterCapability
    from printorian.contexts.scheduling import SchedulablePrinter
    from printorian.drivers import PrinterState

    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(60),
        total_grams=Decimal(20),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1000),
        tolerance=TOLERANCE,
    )
    finishes = clock.now() + timedelta(hours=2)
    busy = SchedulablePrinter(
        capability=PrinterCapability(
            printer_id=str(new_id()),
            state=PrinterState.PRINTING,
            width_mm=Decimal(256),
            depth_mm=Decimal(256),
            height_mm=Decimal(256),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=False,
            loaded=(("PLA", "white", Decimal(800)),),
        ),
        free_at=finishes,
    )
    await production.plan_pass([busy])

    position = await production.queue_position(job.order_id)

    assert position is not None
    assert position.reason == "waitlist.awaiting_capacity"
    assert position.position == 1
    assert position.predicted_start == finishes


async def test_the_position_names_the_machine_and_the_attempt(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """What the cabinet's pipeline dates its «Назначен» stage from.

    The `printer_id` travels rather than a name: `production` knows an id and
    nothing about what a printer *is*, and resolving one crosses into `fleet` —
    which the API layer does, because it is the only layer allowed to know both.
    """
    chosen = new_id()
    await ensure_printer(db_session, chosen, name="P-01")
    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(60),
        total_grams=Decimal(20),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1000),
        tolerance=TOLERANCE,
    )
    free = SchedulablePrinter(
        capability=PrinterCapability(
            printer_id=str(chosen),
            state=PrinterState.IDLE,
            width_mm=Decimal(256),
            depth_mm=Decimal(256),
            height_mm=Decimal(256),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=False,
            loaded=(("PLA", "white", Decimal(800)),),
        )
    )
    await production.plan_pass([free])

    position = await production.queue_position(job.order_id)

    assert position is not None
    assert position.printer_id == chosen
    assert position.attempt == 1
    # Dated by the *database's* clock, not the injected one: `JobEvent.created_at`
    # is a server default, the same split `Session.expires_at` documents. Which
    # means the assertion here is that it happened, not when.
    assert position.assigned_at is not None
    # Assigned, not started: the machine has not confirmed it is running.
    assert position.started_at is None


# ------------------------------------------------- when the waiting stops


async def test_a_job_that_gets_assigned_leaves_the_wait_list(
    production: ProductionService, db_session: AsyncSession, clock: FixedClock
) -> None:
    """The row has to go when the wait *ends*, not only when its reason changes.

    A pass that assigns a job leaves it out of `Plan.wait_list` — it is not
    waiting any more — so a refresh that rewrites only the rows named there keeps
    the old one indefinitely. Nothing else removes it: the farm-wide clear is a
    person pressing a button, and the foreign keys cascade only when the job or
    its order is destroyed. The customer is then shown the reason their work was
    stuck three passes ago, beside a job that is already on a machine.
    """
    machine = new_id()
    await ensure_printer(db_session, machine, name="P-01")
    job = await _prepared(production)
    busy = a_printer(
        printer_id=machine,
        state=PrinterState.PRINTING,
        free_at=clock.now() + timedelta(hours=2),
    )

    # One pass with the only machine mid-print, so the job queues for capacity...
    await production.plan_pass([busy])
    waiting = await production.queue_position(job.order_id)
    assert waiting is not None and waiting.reason == "waitlist.awaiting_capacity"

    # ...and one with it free, which is what takes the job off the list.
    await production.plan_pass([a_printer(printer_id=machine)])

    assert (await production.get(job.id)).status is JobStatus.ASSIGNED
    rows = await db_session.scalars(select(WaitListEntry).where(WaitListEntry.job_id == job.id))
    assert list(rows) == []

    position = await production.queue_position(job.order_id)
    assert position is not None
    assert position.job_status is JobStatus.ASSIGNED
    assert position.reason is None
    assert position.position is None
    assert position.predicted_start is None

    # `reads.wait_list` and the dashboard's chip count rows straight out of the
    # table, so a row left behind is also a job the floor is told is stuck.
    assert await production.wait_list() == []
    assert await wait_list_size(db_session) == 0


async def test_a_job_that_stopped_waiting_stops_counting_against_others(
    production: ProductionService, db_session: AsyncSession, clock: FixedClock
) -> None:
    """A stale row is not only its own customer's problem — it is somebody else's number.

    Position is counted by comparing predicted starts across the whole table, so
    a job that has already been assigned is still one place of queue in front of
    everyone behind it. Two customers and two machines: the small machine comes
    free first and can only take the small job; the big one is hours away and is
    the only thing that will ever take the large job. So the small job is
    predicted to start first and the large one is behind it — until the small job
    is assigned, at which point the large one is first in a queue of one.
    """
    small_id, big_id = new_id(), new_id()
    await ensure_printer(db_session, small_id, name="P-01")
    await ensure_printer(db_session, big_id, name="P-02")
    other_order = new_id()
    await ensure_order(db_session, other_order, number="TEST-2")

    soon, later = clock.now() + timedelta(hours=1), clock.now() + timedelta(hours=3)
    small_busy = a_printer(
        printer_id=small_id,
        state=PrinterState.PRINTING,
        free_at=soon,
        width_mm=Decimal(120),
    )
    big_busy = a_printer(printer_id=big_id, state=PrinterState.PRINTING, free_at=later)

    fits_anywhere = await _prepared(production)
    await _prepared(production, order_id=other_order, width_mm=Decimal(200))

    await production.plan_pass([small_busy, big_busy])
    behind = await production.queue_position(other_order)
    assert behind is not None
    assert behind.position == 2

    # The small machine comes free and takes the only job it can. The large job
    # still waits on the big machine — and is now first, not second.
    await production.plan_pass([a_printer(printer_id=small_id, width_mm=Decimal(120)), big_busy])

    assert (await production.get(fits_anywhere.id)).status is JobStatus.ASSIGNED
    ahead = await production.queue_position(other_order)
    assert ahead is not None
    assert ahead.reason == "waitlist.awaiting_capacity"
    assert ahead.position == 1
    assert await wait_list_size(db_session) == 1
