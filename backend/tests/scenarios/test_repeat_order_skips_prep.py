"""The single most important structural idea in V2 (ARCHITECTURE §4.1).

The first order of a configuration waits for an engineer. Every later order of the
*same* configuration reuses the cached plate and needs no human at all. If this
stops holding, human-gated slicing scales linearly with orders and the farm stops
being a farm — and nothing else in the system would notice.

This composes the two contexts the way a caller must: `catalog` answers "have we
sliced this before", `production` decides what that means for the job. Neither
imports the other; the composition is the caller's, which is what keeps the
boundary intact.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary, PlateStatus, RecordPlate
from printorian.contexts.fleet import PrinterCapability
from printorian.contexts.production import CreateJob, JobStatus, ProductionService
from printorian.contexts.scheduling import SchedulablePrinter
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import EntityId, new_id
from printorian.core.storage import InMemoryObjectStore
from printorian.drivers import JobHandle, PrinterState, RemoteFileRef
from tests.conftest import ensure_order, ensure_printer

TOLERANCE = Decimal("0.15")

#: One customer configuration: this model, this scale, this material, this profile.
CONFIGURATION = {
    "model_hash": "sha-of-the-cube",
    "scale": Decimal(1),
    "material_code": "pla-white",
    "printer_profile": "p1s-0.4-pla",
}


#: The order every intake in this scenario belongs to. It has to exist:
#: PostgreSQL enforces `print_jobs.order_id`, and the scenario is about the
#: plate cache, not about where the order came from.
SEED_ORDER_ID = new_id()

#: The machines the scenario plans onto. `print_jobs.printer_id` is a real
#: foreign key, so a `SchedulablePrinter` DTO is not enough on its own — and
#: there are two, because a pass offered the same machine twice would assign one
#: job and then refuse the second as an invalid transition.
SEED_PRINTER_IDS = [new_id(), new_id()]


@pytest.fixture(autouse=True)
async def _the_order_exists(db_session: AsyncSession) -> None:
    await ensure_order(db_session, SEED_ORDER_ID)
    for index, printer_id in enumerate(SEED_PRINTER_IDS):
        await ensure_printer(db_session, printer_id, name=f"P-{index + 1:02d}")


@pytest.fixture
def library(db_session: AsyncSession, clock: FixedClock) -> PlateLibrary:
    return PlateLibrary(db_session, clock)


@pytest.fixture
def production(
    db_session: AsyncSession,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
) -> ProductionService:
    return ProductionService(db_session, clock, bus, object_store)


#: What the engineer's slicer produced. A stand-in for a real 3MF, but the *bytes*
#: are real: this is what has to reach the printer, and asserting on it is what
#: proves the plate travelled rather than an empty file.
PLATE_BYTES = b"PK-pretend-3mf-for-the-cube"


class _Driver:
    """Records what it was actually given, so the test can check the bytes."""

    def __init__(self) -> None:
        self.received: bytes | None = None

    async def upload(self, plate: object) -> RemoteFileRef:
        self.received = getattr(plate, "content", None)
        return RemoteFileRef(path="/cache/plate.3mf")

    async def start(self, ref: RemoteFileRef, ams_mapping: dict[int, int]) -> JobHandle:
        return JobHandle(value="handle-1")


def an_order_for_the_cube() -> CreateJob:
    return CreateJob(
        order_id=SEED_ORDER_ID,
        material_type="PLA",
        colors=["white"],
        width_mm=Decimal(40),
        depth_mm=Decimal(40),
        height_mm=Decimal(40),
        grams_required=Decimal(50),
        estimated_minutes=Decimal(120),
    )


def a_printer(which: int = 0) -> SchedulablePrinter:
    return SchedulablePrinter(
        capability=PrinterCapability(
            printer_id=str(SEED_PRINTER_IDS[which]),
            state=PrinterState.IDLE,
            width_mm=Decimal(256),
            depth_mm=Decimal(256),
            height_mm=Decimal(256),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=False,
            loaded=(("PLA", "white", Decimal(800)),),
        )
    )


async def _intake(
    library: PlateLibrary, production: ProductionService, order: CreateJob
) -> tuple[EntityId, JobStatus]:
    """What a caller does when an order is paid.

    Ask the library whether this configuration has been sliced. A hit attaches the
    plate and the job is immediately schedulable; a miss leaves it in the prep
    queue for an engineer. This is the whole of ADR-0006's mechanism.
    """
    job = await production.create_job(order)
    plate = await library.find(**CONFIGURATION)  # type: ignore[arg-type]
    if plate is None:
        return job.id, JobStatus.PENDING

    updated = await production.attach_prepared_plate(
        job.id,
        plate_id=plate.id,
        filename=plate.filename,
        print_minutes=plate.print_minutes,
        total_grams=plate.total_grams,
        # The plate is cheaper than quoted here, so the band is not the subject
        # of this test — `test_prep_queue.py` covers the band itself.
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(950),
        tolerance=TOLERANCE,
    )
    return job.id, updated.status


async def _slice_it(
    library: PlateLibrary,
    production: ProductionService,
    store: InMemoryObjectStore,
    job_id: EntityId,
) -> None:
    """What an engineer does: slice, upload the plate, cache it, attach it.

    The upload is the part that makes the rest real. A plate row with numbers and
    no file is a legitimate state — an engineer recording what a slice produced
    before sending it — but it is not dispatchable, so the cached-plate path is
    only proven end to end when the bytes are here.
    """
    stored = await store.put(PLATE_BYTES, suffix="3mf")
    plate = await library.record(
        RecordPlate(
            **CONFIGURATION,  # type: ignore[arg-type]
            model_name="cube.stl",
            print_minutes=Decimal(64),
            filament_grams={"0": Decimal("17.3")},
            filename="cube.3mf",
            content_sha256=stored.digest,
            storage_path=stored.path,
            size_bytes=stored.size_bytes,
            slicer_name="BambuStudio",
            slicer_version="1.9.5",
            profile_version="2026.1",
        )
    )
    await production.attach_prepared_plate(
        job_id,
        plate_id=plate.id,
        filename=plate.filename,
        print_minutes=plate.print_minutes,
        total_grams=plate.total_grams,
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(950),
        tolerance=TOLERANCE,
    )


async def test_the_first_order_waits_for_an_engineer(
    library: PlateLibrary, production: ProductionService
) -> None:
    _, status = await _intake(library, production, an_order_for_the_cube())

    assert status is JobStatus.PENDING
    assert len(await production.prep_queue()) == 1


async def test_the_second_order_of_the_same_configuration_needs_nobody(
    library: PlateLibrary, production: ProductionService, object_store: InMemoryObjectStore
) -> None:
    """The claim ADR-0006 is built on, stated as a test so it cannot quietly stop
    being true."""
    first_id, _ = await _intake(library, production, an_order_for_the_cube())
    await _slice_it(library, production, object_store, first_id)

    _, status = await _intake(library, production, an_order_for_the_cube())

    assert status is JobStatus.READY
    # Nothing is waiting for a person.
    assert await production.prep_queue() == []


async def test_the_repeat_order_reaches_a_printer_with_no_human_action(
    library: PlateLibrary, production: ProductionService, object_store: InMemoryObjectStore
) -> None:
    """Phase 4's exit criterion, for the cached half: payment to a machine
    starting the job, with nobody touching it."""
    first_id, _ = await _intake(library, production, an_order_for_the_cube())
    await _slice_it(library, production, object_store, first_id)

    second_id, status = await _intake(library, production, an_order_for_the_cube())
    assert status is JobStatus.READY

    outcome = await production.plan_pass([a_printer(0), a_printer(1)])
    assert outcome.assigned >= 1

    driver = _Driver()
    dispatched = await production.dispatch(second_id, driver)
    assert dispatched.status is JobStatus.PRINTING
    # The whole point: the machine was sent the plate the engineer sliced for the
    # *first* order, read back out of the object store. An empty upload here would
    # be a printer that starts and produces nothing.
    assert driver.received == PLATE_BYTES


async def test_the_repeat_order_uses_the_slicer_numbers_not_the_mesh_guess(
    library: PlateLibrary, production: ProductionService, object_store: InMemoryObjectStore
) -> None:
    first_id, _ = await _intake(library, production, an_order_for_the_cube())
    await _slice_it(library, production, object_store, first_id)

    second_id, _ = await _intake(library, production, an_order_for_the_cube())
    job = await production.get(second_id)

    # 120 minutes was the mesh estimate the order was priced from; 64 is the truth.
    assert job.estimated_minutes == Decimal(64)
    assert job.grams_required == Decimal("17.3")


async def test_a_changed_profile_sends_the_next_order_back_to_an_engineer(
    library: PlateLibrary, production: ProductionService, object_store: InMemoryObjectStore
) -> None:
    """Plates invalidate when the profile moves (ADR-0006). Reusing one after that
    would print from settings somebody has already replaced."""
    first_id, _ = await _intake(library, production, an_order_for_the_cube())
    await _slice_it(library, production, object_store, first_id)
    await library.invalidate_profile(str(CONFIGURATION["printer_profile"]))

    _, status = await _intake(library, production, an_order_for_the_cube())

    assert status is JobStatus.PENDING


async def test_a_rejected_plate_is_never_reused(
    library: PlateLibrary, production: ProductionService, object_store: InMemoryObjectStore
) -> None:
    first_id, _ = await _intake(library, production, an_order_for_the_cube())
    await _slice_it(library, production, object_store, first_id)
    plate = await library.find(**CONFIGURATION)  # type: ignore[arg-type]
    assert plate is not None
    await library.invalidate(plate.id, status=PlateStatus.REJECTED)

    _, status = await _intake(library, production, an_order_for_the_cube())

    assert status is JobStatus.PENDING
