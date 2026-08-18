"""The prep queue and the variance band.

ADR-0006 says the first order of a configuration waits for an engineer and every
later one does not. ADR-0013 says the truth slicing produces may cost more than the
quote, and that past a configured band the job stops instead of quietly eating the
difference.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production import (
    CreateJob,
    EstimateVariance,
    JobStatus,
    ProductionService,
    assess_variance,
)
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from tests.conftest import ensure_order, ensure_plate

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


# ------------------------------------------------------ the variance band


def test_a_plate_within_tolerance_is_absorbed() -> None:
    verdict = assess_variance(
        quoted_cost=Decimal(1000), prepared_cost=Decimal(1100), tolerance=TOLERANCE
    )

    assert verdict.within_tolerance
    assert verdict.delta == Decimal(100)


def test_exactly_at_the_tolerance_is_still_absorbed() -> None:
    """The band is inclusive: 15% over on a 15% tolerance is not an escalation."""
    verdict = assess_variance(
        quoted_cost=Decimal(1000), prepared_cost=Decimal(1150), tolerance=TOLERANCE
    )

    assert verdict.within_tolerance


def test_beyond_the_band_the_job_stops() -> None:
    verdict = assess_variance(
        quoted_cost=Decimal(1000), prepared_cost=Decimal(1200), tolerance=TOLERANCE
    )

    assert not verdict.within_tolerance
    assert verdict.ratio == Decimal("0.2")


def test_a_cheaper_plate_is_never_an_escalation() -> None:
    """One-sided on purpose: the quote is what the customer agreed to, and the
    farm keeping a saving is not something to route to a human."""
    verdict = assess_variance(
        quoted_cost=Decimal(1000), prepared_cost=Decimal(400), tolerance=TOLERANCE
    )

    assert verdict.within_tolerance
    assert verdict.delta == Decimal(-600)


def test_a_free_job_cannot_be_exceeded_by_a_percentage() -> None:
    """Zero would otherwise divide, or read as an infinite overrun. A free job is
    a decision somebody already made."""
    verdict = assess_variance(
        quoted_cost=Decimal(0), prepared_cost=Decimal(500), tolerance=TOLERANCE
    )

    assert verdict.within_tolerance
    assert verdict.ratio == Decimal(0)


def test_the_tolerance_is_a_parameter_not_a_constant() -> None:
    """ADR-0013: configuration, not a number buried in code."""
    strict = assess_variance(
        quoted_cost=Decimal(1000), prepared_cost=Decimal(1100), tolerance=Decimal("0.05")
    )
    generous = assess_variance(
        quoted_cost=Decimal(1000), prepared_cost=Decimal(1100), tolerance=Decimal("0.50")
    )

    assert not strict.within_tolerance
    assert generous.within_tolerance


# ---------------------------------------------------------- the prep queue


async def test_a_new_job_is_in_the_prep_queue(production: ProductionService) -> None:
    job = await production.create_job(a_job())

    queue = await production.prep_queue()
    assert [entry.id for entry in queue] == [job.id]


async def test_the_queue_is_ordered_by_what_is_due_first(
    production: ProductionService, clock: FixedClock
) -> None:
    relaxed = await production.create_job(a_job(due_at=clock.now() + timedelta(days=5)))
    urgent = await production.create_job(a_job(due_at=clock.now() + timedelta(hours=2)))
    undated = await production.create_job(a_job())

    queue = await production.prep_queue()

    assert [entry.id for entry in queue] == [urgent.id, relaxed.id, undated.id]


async def test_a_sliced_job_leaves_the_queue(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(72),
        total_grams=Decimal(17),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1000),
        tolerance=TOLERANCE,
    )

    assert await production.prep_queue() == []


# ------------------------------------------------------ attaching a plate


async def test_an_attached_plate_makes_the_job_schedulable(
    production: ProductionService,
) -> None:
    job = await production.create_job(a_job())

    result = await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(72),
        total_grams=Decimal(17),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1050),
        tolerance=TOLERANCE,
    )

    assert result.status is JobStatus.READY
    assert result.plate_filename == "cube.3mf"


async def test_the_job_reschedules_against_the_slicer_numbers(
    production: ProductionService,
) -> None:
    """The plate is the better truth. Leaving the mesh guess in place would plan
    capacity against a number nobody believes any more."""
    job = await production.create_job(a_job())

    result = await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(95),
        total_grams=Decimal("21.5"),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1000),
        tolerance=TOLERANCE,
    )

    assert result.estimated_minutes == Decimal(95)
    assert result.grams_required == Decimal("21.5")


async def test_a_plate_beyond_tolerance_holds_the_job(production: ProductionService) -> None:
    """It does not dispatch and it is not merely pending — nothing is waiting to
    be sliced, somebody has to decide about money."""
    job = await production.create_job(a_job())

    result = await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(200),
        total_grams=Decimal(60),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1400),
        tolerance=TOLERANCE,
    )

    assert result.status is JobStatus.ON_HOLD


async def test_a_held_job_is_not_planned(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(200),
        total_grams=Decimal(60),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1400),
        tolerance=TOLERANCE,
    )

    outcome = await production.plan_pass([])

    assert outcome.considered == 0
    assert (await production.get(job.id)).status is JobStatus.ON_HOLD


async def test_an_exceeded_variance_is_announced_not_acted_on(
    production: ProductionService, bus: EventBus
) -> None:
    """`PriceReview` is a state in the *order* machine. Production says what it
    found and lets ordering decide, rather than reaching across the boundary."""
    job = await production.create_job(a_job())

    async with bus.collecting() as events:
        await production.attach_prepared_plate(
            job.id,
            plate_id=SEED_PLATE_ID,
            filename="cube.3mf",
            print_minutes=Decimal(200),
            total_grams=Decimal(60),
            quoted_cost=Decimal(1000),
            prepared_cost=Decimal(1400),
            tolerance=TOLERANCE,
        )

    published = {event.name for event in events}
    assert "plate.variance_exceeded" in published
    assert "job.ready" not in published


async def test_releasing_a_hold_lets_the_job_through(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(200),
        total_grams=Decimal(60),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1400),
        tolerance=TOLERANCE,
    )

    released = await production.release_hold(job.id)

    assert released.status is JobStatus.READY


# ------------------------------------------------------------- recording


async def test_every_variance_is_recorded_even_within_tolerance(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """ADR-0013. The ones inside the band are what calibrates the estimator;
    keeping only escalations would teach it from its worst cases alone."""
    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(130),
        total_grams=Decimal(52),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1020),
        tolerance=TOLERANCE,
    )

    rows = list(
        await db_session.scalars(select(EstimateVariance).where(EstimateVariance.job_id == job.id))
    )
    assert len(rows) == 1
    assert rows[0].within_tolerance is True


async def test_the_record_keeps_the_manufacturing_numbers_behind_the_money(
    production: ProductionService, db_session: AsyncSession
) -> None:
    """The estimator predicts minutes and grams, not roubles — so calibration
    needs both sides of those, not just the prices."""
    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(130),
        total_grams=Decimal(52),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1020),
        tolerance=TOLERANCE,
    )

    row = await db_session.scalar(select(EstimateVariance).where(EstimateVariance.job_id == job.id))
    assert row is not None
    assert row.estimated_minutes == Decimal(120)
    assert row.prepared_minutes == Decimal(130)
    assert row.estimated_grams == Decimal(50)
    assert row.prepared_grams == Decimal(52)


async def test_the_job_history_shows_why_it_was_held(production: ProductionService) -> None:
    job = await production.create_job(a_job())
    await production.attach_prepared_plate(
        job.id,
        plate_id=SEED_PLATE_ID,
        filename="cube.3mf",
        print_minutes=Decimal(200),
        total_grams=Decimal(60),
        quoted_cost=Decimal(1000),
        prepared_cost=Decimal(1400),
        tolerance=TOLERANCE,
    )

    final = await production.get(job.id)
    held = next(event for event in final.events if event.to_status == "on_hold")
    assert held.reason == "plate.variance_exceeded"
    assert held.details["overrun_ratio"] == "0.4"
