"""Planning passes and dispatch.

The load-bearing behaviour is honesty about failure: a dispatch that did not
happen must leave the job schedulable and say why — never printing, never quietly
successful — and every planning outcome must be explainable afterwards.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import PrinterCapability
from printorian.contexts.production import (
    CreateJob,
    JobStatus,
    PrintJob,
    ProductionService,
    WaitListEntry,
)
from printorian.contexts.production.service import (
    DISPATCH_NO_PLATE,
    DISPATCH_START_FAILED,
    DISPATCH_UPLOAD_FAILED,
)
from printorian.contexts.scheduling import (
    WAIT_AWAITING_CAPACITY,
    WAIT_MATERIAL_NOT_LOADED,
    SchedulablePrinter,
)
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from printorian.drivers import JobHandle, PrinterState, RemoteFileRef
from tests.conftest import ensure_order, ensure_printer

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


#: Machines the planner is allowed to assign to.
#:
#: `SchedulablePrinter` is a DTO, not a row, but `print_jobs.printer_id` is a real
#: foreign key — so the fleet has to actually contain them.
SEED_PRINTER_IDS = [new_id() for _ in range(4)]


@pytest.fixture(autouse=True)
async def _the_printers_exist(db_session: AsyncSession) -> None:
    for index, printer_id in enumerate(SEED_PRINTER_IDS):
        await ensure_printer(db_session, printer_id, name=f"P-{index + 1:02d}")


@pytest.fixture
def production(
    db_session: AsyncSession,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> ProductionService:
    return ProductionService(db_session, clock, bus, object_store)


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


def a_printer(printer_id: str | None = None, **overrides: object) -> SchedulablePrinter:
    # A real printer id, because `PrintJob.printer_id` is one. A readable stand-in
    # would only fail at the point the service stores it.
    capability = PrinterCapability(
        printer_id=printer_id or str(SEED_PRINTER_IDS[0]),
        state=overrides.pop("state", PrinterState.IDLE),  # type: ignore[arg-type]
        width_mm=overrides.pop("width_mm", Decimal(256)),  # type: ignore[arg-type]
        depth_mm=Decimal(256),
        height_mm=Decimal(256),
        nozzle_diameter_mm=Decimal("0.4"),
        supports_multi_material=False,
        loaded=overrides.pop("loaded", (("PLA", "white", Decimal(800)),)),  # type: ignore[arg-type]
    )
    return SchedulablePrinter(capability=capability, **overrides)  # type: ignore[arg-type]


class _Driver:
    """A driver stand-in whose failures are chosen by the test."""

    def __init__(self, *, fail_upload: bool = False, fail_start: bool = False) -> None:
        self.fail_upload = fail_upload
        self.fail_start = fail_start
        self.uploaded = False
        self.started = False

    async def upload(self, plate: object) -> RemoteFileRef:
        if self.fail_upload:
            raise RuntimeError("no storage")
        self.uploaded = True
        return RemoteFileRef(path="/cache/plate.3mf")

    async def start(self, ref: RemoteFileRef, ams_mapping: dict[int, int]) -> JobHandle:
        if self.fail_start:
            raise RuntimeError("refused")
        self.started = True
        return JobHandle(value="job-1")


# -------------------------------------------------------------- planning


async def test_a_planning_pass_assigns_a_ready_job(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")

    outcome = await production.plan_pass([a_printer()])

    assert outcome.assigned == 1
    assert (await production.get(job.id)).status is JobStatus.ASSIGNED


async def test_a_pending_job_is_not_planned(production: ProductionService) -> None:
    """No plate, nothing to send. Scheduling it would dispatch an empty upload."""
    await production.create_job(a_job())

    outcome = await production.plan_pass([a_printer()])

    assert outcome.considered == 0
    assert outcome.assigned == 0


async def test_the_decision_is_persisted_with_every_candidate(
    production: ProductionService,
) -> None:
    """ "Why did this job go there" has to be answerable from the database."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    small_id, ok_id = str(SEED_PRINTER_IDS[1]), str(SEED_PRINTER_IDS[2])
    too_small = a_printer(small_id, width_mm=Decimal(10))

    await production.plan_pass([too_small, a_printer(ok_id)])

    records = await production.decisions_for(job.id)
    assert len(records) == 1
    assert str(records[0].chosen_printer_id) == ok_id
    rejected = {c["printer_id"]: c["reasons"] for c in records[0].candidates if not c["eligible"]}
    assert "reject.build_volume" in rejected[small_id]


async def test_a_decision_is_recorded_even_when_nothing_was_assigned(
    production: ProductionService,
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")

    await production.plan_pass([])

    assert len(await production.decisions_for(job.id)) == 1


async def test_an_unschedulable_job_is_wait_listed_with_a_reason(
    production: ProductionService,
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    wrong_material = a_printer(loaded=(("PETG", "white", Decimal(800)),))

    outcome = await production.plan_pass([wrong_material])

    assert outcome.wait_listed == 1
    entry = (await production.wait_list())[0]
    assert entry.reason == WAIT_MATERIAL_NOT_LOADED
    # Nothing knows when a person will mount a spool, so no date is offered.
    assert entry.predicted_start is None


async def test_a_capacity_wait_carries_a_predicted_start(
    production: ProductionService, clock: FixedClock
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    finishes = clock.now() + timedelta(hours=2)
    busy = a_printer(state=PrinterState.PRINTING, free_at=finishes)

    await production.plan_pass([busy])

    entry = (await production.wait_list())[0]
    assert entry.reason == WAIT_AWAITING_CAPACITY
    assert entry.predicted_start == finishes


async def test_re_planning_replaces_the_wait_list_row(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """Otherwise the cabinet shows a customer a stale reason beside a current one."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    wrong_material = a_printer(loaded=(("PETG", "white", Decimal(800)),))

    await production.plan_pass([wrong_material])
    await production.plan_pass([wrong_material])

    rows = list(
        await db_session.scalars(select(WaitListEntry).where(WaitListEntry.job_id == job.id))
    )
    assert len(rows) == 1


async def test_a_machine_taken_in_this_pass_is_not_given_a_second_job(
    production: ProductionService,
) -> None:
    first = await production.create_job(a_job())
    second = await production.create_job(a_job())
    await production.mark_ready(first.id, plate_filename="a.3mf")
    await production.mark_ready(second.id, plate_filename="b.3mf")

    outcome = await production.plan_pass([a_printer()])

    assert outcome.assigned == 1
    assert outcome.wait_listed == 1


# -------------------------------------------------------------- dispatch


async def test_a_successful_dispatch_starts_the_print(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])

    driver = _Driver()
    dispatched = await production.dispatch(job.id, driver)

    assert driver.uploaded and driver.started
    assert dispatched.status is JobStatus.PRINTING
    assert dispatched.started_at is not None


async def test_a_failed_upload_puts_the_job_back_in_the_queue(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    """Nothing reached a bed and no material was spent, so another machine can try."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])

    result = await production.dispatch(job.id, _Driver(fail_upload=True))

    assert result.status is JobStatus.READY
    assert result.failure_code == DISPATCH_UPLOAD_FAILED
    assert result.printer_id is None


async def test_a_refused_start_puts_the_job_back_too(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])

    result = await production.dispatch(job.id, _Driver(fail_start=True))

    assert result.status is JobStatus.READY
    assert result.failure_code == DISPATCH_START_FAILED


async def test_a_machine_with_no_driver_never_pretends_to_print(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    """ADR-0007. A manual printer is driven by a human, and saying so is the
    honest outcome — inventing a dispatch is not."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])

    result = await production.dispatch(job.id, None)

    assert result.status is JobStatus.READY
    assert result.failure_code == "error.driver.unavailable"


async def test_a_job_without_a_plate_is_never_uploaded(
    production: ProductionService,
    db_session: AsyncSession,
    with_plate: Callable[..., Awaitable[None]],
) -> None:
    """A filename is not a plate. Only bytes are.

    The job keeps `plate_filename` throughout — what goes missing is the prepared
    plate the bytes hang off. That is the distinction that matters: dispatch reads
    the file from the object store, so a job naming a plate it no longer has must
    fail here rather than uploading nothing and starting a print that produces
    nothing (ADR-0007).
    """
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    # The plate goes missing between planning and dispatch. Flushed, because the
    # service re-reads the row with `populate_existing=True`.
    stored = await db_session.scalar(select(PrintJob).where(PrintJob.id == job.id))
    assert stored is not None
    stored.prepared_plate_id = None
    await db_session.flush()

    driver = _Driver()
    result = await production.dispatch(job.id, driver)

    assert result.failure_code == DISPATCH_NO_PLATE
    assert not driver.uploaded


async def test_a_failed_dispatch_is_recorded_not_swallowed(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    await production.dispatch(job.id, _Driver(fail_upload=True))

    final = await production.get(job.id)
    assert any(event.reason == "dispatch.failed" for event in final.events)
