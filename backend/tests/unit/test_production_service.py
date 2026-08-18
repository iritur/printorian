"""A job's life: created, prepared, run, finished — or failed and remade.

Planning and dispatch live in `test_production_planning.py`; this file is about
the state machine and what each step records.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import PrinterCapability
from printorian.contexts.production import (
    CreateJob,
    JobStatus,
    ProductionService,
)
from printorian.contexts.scheduling import (
    SchedulablePrinter,
)
from printorian.core.clock import FixedClock
from printorian.core.errors import DomainRuleViolationError
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


# ------------------------------------------------------------- lifecycle


async def test_a_new_job_waits_for_a_plate(production: ProductionService) -> None:
    """Slicing is human-gated (ADR-0006), so nothing is schedulable on creation."""
    job = await production.create_job(a_job())

    assert job.status is JobStatus.PENDING


async def test_a_prepared_plate_makes_the_job_schedulable(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    ready = await production.mark_ready(job.id, plate_filename="cube.3mf")

    assert ready.status is JobStatus.READY
    assert ready.plate_filename == "cube.3mf"


async def test_the_lifecycle_refuses_a_move_it_does_not_allow(
    production: ProductionService,
) -> None:
    job = await production.create_job(a_job())

    with pytest.raises(DomainRuleViolationError):
        await production.complete(job.id)


async def test_every_step_is_recorded_in_order(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    final = await production.get(job.id)

    assert [event.to_status for event in final.events] == ["pending", "ready"]
    assert [event.sequence for event in final.events] == [0, 1]


# --------------------------------------------------------------- outcome


async def test_finishing_a_print_releases_the_machine(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    await production.dispatch(job.id, _Driver())

    done = await production.complete(job.id)

    assert done.status is JobStatus.SUCCEEDED
    assert done.printer_id is None
    assert done.progress_percent == 100


async def test_a_finished_print_announces_the_machine_is_free(
    production: ProductionService, bus: EventBus, with_plate: Callable[..., Awaitable[None]]
) -> None:
    """The scheduler's own trigger — waiting for the next tick would leave a
    printer idle with work in the queue."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    await production.dispatch(job.id, _Driver())

    async with bus.collecting() as events:
        await production.complete(job.id)

    assert "printer.became_free" in [event.name for event in events]


async def test_a_failed_print_is_a_failure_with_a_code(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    await production.dispatch(job.id, _Driver())

    failed = await production.fail(job.id, code="error.print.spaghetti")

    assert failed.status is JobStatus.FAILED
    assert failed.failure_code == "error.print.spaghetti"
    assert failed.printer_id is None


async def test_a_remake_is_another_attempt_at_the_same_job(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    """Not a new job: the attempts stay attached to what the customer ordered."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    await production.dispatch(job.id, _Driver())
    await production.fail(job.id, code="error.print.spaghetti")

    remade = await production.remake(job.id)

    assert remade.status is JobStatus.READY
    assert remade.attempt == 2
    assert remade.progress_percent is None


async def test_a_remade_job_is_schedulable_again(
    production: ProductionService, with_plate: Callable[..., Awaitable[None]]
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    await production.dispatch(job.id, _Driver())
    await production.fail(job.id, code="error.print.spaghetti")
    await production.remake(job.id)

    outcome = await production.plan_pass([a_printer()])

    assert outcome.assigned == 1


async def test_progress_is_clamped_to_a_sane_range(production: ProductionService) -> None:
    """A driver reporting 130% must not reach a customer's progress bar."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")

    assert (await production.record_progress(job.id, percent=130)).progress_percent == 100
    assert (await production.record_progress(job.id, percent=-5)).progress_percent == 0


async def test_cancelling_releases_the_machine(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await production.plan_pass([a_printer()])

    cancelled = await production.cancel(job.id, reason="customer.withdrew")

    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.printer_id is None


async def test_a_due_job_is_planned_before_a_relaxed_one(
    production: ProductionService, clock: FixedClock
) -> None:
    relaxed = await production.create_job(a_job(due_at=clock.now() + timedelta(days=5)))
    urgent = await production.create_job(a_job(due_at=clock.now() + timedelta(hours=1)))
    await production.mark_ready(relaxed.id, plate_filename="a.3mf")
    await production.mark_ready(urgent.id, plate_filename="b.3mf")

    await production.plan_pass([a_printer()])

    assert (await production.get(urgent.id)).status is JobStatus.ASSIGNED
    assert (await production.get(relaxed.id)).status is JobStatus.READY


async def test_a_job_event_carries_the_printer_it_was_given(
    production: ProductionService,
) -> None:
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    printer_id = str(SEED_PRINTER_IDS[3])
    await production.plan_pass([a_printer(printer_id)])

    final = await production.get(job.id)
    assigned = next(event for event in final.events if event.to_status == "assigned")
    assert assigned.details["printer_id"] == printer_id


async def test_the_clock_is_the_injected_one(
    production: ProductionService, clock: FixedClock, with_plate: Callable[..., Awaitable[None]]
) -> None:
    """No `datetime.now()` anywhere in the service — tests must control time."""
    job = await production.create_job(a_job())
    await production.mark_ready(job.id, plate_filename="cube.3mf")
    await with_plate(job.id)
    await production.plan_pass([a_printer()])
    await production.dispatch(job.id, _Driver())

    assert (await production.get(job.id)).started_at == clock.now()
