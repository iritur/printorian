"""Every production sort is a total order.

Kept apart from `test_production_planning.py`, which is about what the planner
*decides*; these are about the queries that decide what it decides *over*, and
about the record that afterwards explains it. A single-column sort on a timestamp
is the flake class this module exists to hold shut — the same shape that once had
CI and a dev machine disagreeing about which of two settings edits came first.

**The premise is asserted rather than assumed.** `Entity.created_at` is a
`server_default` of `now()`, and PostgreSQL's `now()` is the *transaction's* start,
so every row a test writes carries the identical timestamp — which is what makes
the tie reachable here at all. `test_rows_written_together_share_a_timestamp`
states that out loud, so that if it ever stops being true these tests announce it
rather than passing for a reason nobody chose. `FixedClock` freezes the injected
clock over the top of it, which is why `predicted_start` ties too.

What is asserted is the documented contract — "sorted by id" — and never "in the
order they were created". Those are not the same claim: `new_id` is a UUIDv7 with
millisecond resolution, so rows created inside one millisecond sort by their random
bits. That is still a total order, and a total order is the whole requirement.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import PrinterCapability
from printorian.contexts.production import CreateJob, JobStatus, PrintJob, ProductionService
from printorian.contexts.production.planning import claim_ready_jobs
from printorian.contexts.production.reads import decisions_for
from printorian.contexts.scheduling import SchedulablePrinter
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import EntityId, new_id
from printorian.core.storage import InMemoryObjectStore
from printorian.drivers import PrinterState
from tests.conftest import ensure_order, ensure_printer

SEED_ORDER_ID = new_id()
SEED_PRINTER_ID = new_id()


@pytest.fixture(autouse=True)
async def _the_order_and_printer_exist(db_session: AsyncSession) -> None:
    await ensure_order(db_session, SEED_ORDER_ID)
    await ensure_printer(db_session, SEED_PRINTER_ID, name="P-01")


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


def a_printer(**overrides: object) -> SchedulablePrinter:
    capability = PrinterCapability(
        printer_id=str(SEED_PRINTER_ID),
        state=overrides.pop("state", PrinterState.IDLE),  # type: ignore[arg-type]
        width_mm=Decimal(256),
        depth_mm=Decimal(256),
        height_mm=Decimal(256),
        nozzle_diameter_mm=Decimal("0.4"),
        supports_multi_material=False,
        loaded=(("PLA", "white", Decimal(800)),),
    )
    return SchedulablePrinter(capability=capability, **overrides)  # type: ignore[arg-type]


async def ready_jobs(
    production: ProductionService, count: int, **overrides: object
) -> list[EntityId]:
    """`count` jobs with a plate on them, so the planner will consider them."""
    ids = []
    for index in range(count):
        job = await production.create_job(a_job(**overrides))
        await production.mark_ready(job.id, plate_filename=f"cube-{index}.3mf")
        ids.append(job.id)
    return ids


# --------------------------------------------------- the premise these rest on


async def test_rows_written_together_share_a_timestamp(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """The tie is real rather than contrived — and every test below depends on it.

    If this ever fails, the others have stopped exercising what they claim to: a
    sort that never ties cannot show a tiebreak working. Read off the column rather
    than off `JobView`, which does not carry it — the point is what PostgreSQL
    stored, not what the API chooses to show.
    """
    await ready_jobs(production, 4)

    stamps = set(await db_session.scalars(select(PrintJob.created_at)))

    assert len(stamps) == 1


# ------------------------------------------------------------------- the batch


async def test_the_ready_batch_is_taken_in_id_order_when_the_clock_ties(
    production: ProductionService, db_session: AsyncSession
) -> None:
    ids = await ready_jobs(production, 5)

    taken = await claim_ready_jobs(db_session)

    assert [job.id for job in taken] == sorted(ids)


async def test_the_batch_boundary_is_the_same_on_every_pass(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """The point of the tiebreak: which jobs a *bounded* batch sees.

    Without a total order, the three the planner takes are three of five chosen by
    the planner's whim, and the two left out wait another pass for a reason nobody
    can state afterwards.
    """
    ids = await ready_jobs(production, 5)

    first = await claim_ready_jobs(db_session, limit=3)
    second = await claim_ready_jobs(db_session, limit=3)

    assert [job.id for job in first] == sorted(ids)[:3]
    assert [job.id for job in second] == [job.id for job in first]


async def test_priority_still_outranks_the_tiebreak(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """`id` breaks ties; it does not compete with the terms above it."""
    ordinary = await ready_jobs(production, 3)
    urgent = await ready_jobs(production, 2, priority=10)

    taken = await claim_ready_jobs(db_session)

    assert [job.id for job in taken] == sorted(urgent) + sorted(ordinary)


# ------------------------------------------------------- the record afterwards


async def test_decisions_for_one_job_are_a_total_order(production: ProductionService) -> None:
    """Three passes, one frozen instant, one answer.

    `AssignmentRecord.created_at` is the transaction's `now()`, so all three
    records carry the same timestamp. A table whose whole purpose is explaining the
    order things were considered in must not be able to answer differently twice.
    """
    (job_id,) = await ready_jobs(production, 1)
    for _ in range(3):
        await production.plan_pass([])

    records = await production.decisions_for(job_id)

    assert len(records) == 3
    assert [row.id for row in records] == sorted((row.id for row in records), reverse=True)
    again = await production.decisions_for(job_id)
    assert [row.id for row in again] == [row.id for row in records]


async def test_the_newest_decisions_survive_the_limit(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """The bound drops the oldest — which only means anything if "oldest" is defined.

    Called through `reads.decisions_for` rather than the service, which fixes the
    limit at `DECISION_LIMIT`; the bound is the thing under test here.
    """
    (job_id,) = await ready_jobs(production, 1)
    for _ in range(4):
        await production.plan_pass([])

    every = await decisions_for(db_session, job_id)
    bounded = await decisions_for(db_session, job_id, limit=2)

    assert len(every) == 4
    assert [row.id for row in bounded] == [row.id for row in every[:2]]


# ---------------------------------------------------------------- the waitlist


async def test_the_wait_list_is_ordered_when_predictions_tie(
    production: ProductionService, clock: FixedClock
) -> None:
    """Predictions tie by construction: one busy machine, one free-at, every job.

    And the jobs waiting on a person tie harder still — `predicted_start` is NULL
    for all of them. Both halves have to be a total order, and the halves have to
    stay in that order relative to each other.

    The order asserted is the entries' own — not their jobs'. A wait-list row is
    written per job inside one planning pass, so its key is minted in the same
    millisecond as its neighbour's and the two do *not* land in job order. That is
    the whole reason the sort needs a term at all, and asserting job order here
    would be asserting a guarantee UUIDv7 does not give.
    """
    free_at = clock.now() + timedelta(hours=2)
    waiting_on_time = await ready_jobs(production, 2)
    waiting_on_a_person = await ready_jobs(production, 2, material_type="PETG")

    await production.plan_pass([a_printer(state=PrinterState.PRINTING, free_at=free_at)])

    entries = await production.wait_list()
    again = await production.wait_list()

    assert [entry.id for entry in again] == [entry.id for entry in entries]
    # Waiting on time first, waiting on a person after — and each half in key order.
    assert {entry.job_id for entry in entries[:2]} == set(waiting_on_time)
    assert {entry.job_id for entry in entries[2:]} == set(waiting_on_a_person)
    assert [entry.id for entry in entries[:2]] == sorted(entry.id for entry in entries[:2])
    assert [entry.id for entry in entries[2:]] == sorted(entry.id for entry in entries[2:])
    assert {entry.predicted_start for entry in entries[:2]} == {free_at}
    assert {entry.predicted_start for entry in entries[2:]} == {None}


async def test_a_planned_job_leaves_the_ready_batch(production: ProductionService) -> None:
    """Guards the tests above: the batch is "ready", so an assignment removes a row."""
    (job_id,) = await ready_jobs(production, 1)

    await production.plan_pass([a_printer()])

    assert (await production.get(job_id)).status is JobStatus.ASSIGNED
