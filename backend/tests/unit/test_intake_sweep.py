"""Paid orders becoming print jobs, with nobody clicking anything.

ROADMAP Phase 4's exit criterion says a paid order reaches a printer with no human
action. Until `workers/intake.py` existed, nothing outside the test suite created a
`PrintJob` at all, and an order stopped dead at `PAID`.

Two branches matter and both are here: the order becomes jobs, and it becomes them
*carrying the geometry it was priced from*. The second is the one with a silent
failure mode — a job whose `model_hash` is missing slices, prints and ships
correctly, and quietly sends every repeat of that configuration back through an
engineer for ever, because `plate_key` can never match.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog.models import ModelAsset
from printorian.contexts.ordering import OrderingService, OrderStatus
from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.production import JobStatus, ProductionService
from printorian.contexts.production.models import PrintJob
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import EntityId, new_id
from printorian.workers.intake import IntakeSweep

#: The digest the plate cache is keyed on. A real-shaped hex string rather than a
#: word, because it is carried onto the job and compared there.
CUBE_SHA = "b" * 64

#: `model_assets.last_used_at` is NOT NULL — retention counts from it rather than
#: from `created_at`. A fixed instant: nothing here asserts on it, and a wall clock
#: in a fixture is a test that fails at midnight.
SOME_TIME = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def sweep(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> IntakeSweep:
    return IntakeSweep(
        db_session,
        ProductionService(db_session, clock, bus),
        OrderingService(db_session, clock, bus),
    )


async def an_asset(db: AsyncSession, *, sha256: str = CUBE_SHA) -> EntityId:
    """One uploaded mesh. `order_lines.model_asset_id` is a real foreign key."""
    asset = ModelAsset(
        id=new_id(), sha256=sha256, original_filename="cube.stl", last_used_at=SOME_TIME
    )
    db.add(asset)
    await db.flush()
    return asset.id


async def a_paid_order(
    db: AsyncSession,
    *,
    number: str,
    asset_id: EntityId | None,
    quantity: int = 1,
    status: OrderStatus = OrderStatus.PAID,
    scale: Decimal = Decimal(1),
    mesh: dict[str, object] | None = None,
) -> EntityId:
    """An order sitting where `payments` leaves it, with one line on it."""
    order = Order(id=new_id(), number=number, status=status)
    db.add(order)
    await db.flush()
    db.add(
        OrderLine(
            order_id=order.id,
            model_name="cube.stl",
            model_asset_id=asset_id,
            material_code="pla-white",
            quantity=quantity,
            scale=scale,
            mesh=dict(mesh) if mesh is not None else {},
            colors=["white"],
            estimated_minutes=Decimal(120),
            estimated_grams=Decimal(50),
            line_total=Decimal(1000),
        )
    )
    await db.flush()
    return order.id


async def jobs_for(db: AsyncSession, order_id: EntityId) -> list[PrintJob]:
    return list(await db.scalars(select(PrintJob).where(PrintJob.order_id == order_id)))


async def test_a_paid_order_becomes_a_job_carrying_what_it_was_priced_from(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """The whole point: no human acted, and the job knows its geometry.

    `model_asset_id` and `model_hash` are asserted together and deliberately. The
    columns and the foreign keys have been in place since the schema was written;
    what was missing was anything that filled them outside a test.
    """
    asset_id = await an_asset(db_session)
    order_id = await a_paid_order(db_session, number="INTAKE-1", asset_id=asset_id)

    outcome = await sweep.sweep()

    assert outcome == type(outcome)(raised=1, jobs=1, failed=0)
    (job,) = await jobs_for(db_session, order_id)
    assert job.model_asset_id == asset_id
    assert job.model_hash == CUBE_SHA
    assert job.status is JobStatus.PENDING

    order = await db_session.get(Order, order_id)
    assert order is not None
    assert order.status is OrderStatus.PREP


async def test_the_line_quantity_reaches_the_job(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """Three of a part is three parts' worth of filament and time.

    `OrderLine.estimated_grams` is per unit — the reading `workers/packaging.py`
    already takes of the same column. A job carrying the per-unit figure would
    have the planner reserve a third of the filament the plate actually needs.
    """
    asset_id = await an_asset(db_session)
    order_id = await a_paid_order(db_session, number="INTAKE-2", asset_id=asset_id, quantity=3)

    await sweep.sweep()

    (job,) = await jobs_for(db_session, order_id)
    assert job.grams_required == Decimal(150)
    assert job.estimated_minutes == Decimal(360)


async def test_the_job_carries_the_part_at_the_size_it_was_ordered(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """A 40 mm mesh at scale 3 is a 120 mm part, and the planner has to be told so.

    The bounding box stored on the line is the box of the **unscaled** mesh —
    `_pricing_spec` writes `analysis.bounding_box` verbatim while `estimate()`
    applies the scale only to volume, mass and time. `_dimensions` used to copy it
    across untouched, so `fleet.can_take`'s only geometric test
    (`job.width_mm > printer.width_mm`) judged every machine in the farm against a
    third of the real part, and the correctly-scaled plate went to a printer that
    could not hold it.

    Drop the multiplication in `core.geometry.scaled_box` and this asserts 40.
    """
    asset_id = await an_asset(db_session)
    order_id = await a_paid_order(
        db_session,
        number="INTAKE-SCALE",
        asset_id=asset_id,
        scale=Decimal(3),
        mesh={"bounding_box_mm": {"x": "40", "y": "30", "z": "20"}},
    )

    await sweep.sweep()

    (job,) = await jobs_for(db_session, order_id)
    assert job.width_mm == Decimal(120)
    assert job.depth_mm == Decimal(90)
    assert job.height_mm == Decimal(60)


async def test_a_job_whose_part_was_never_measured_carries_no_box(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """Zero, not a guess — and the reason it is safe only on this path.

    An invented box would have the planner refuse machines that would have fitted,
    or accept one that would not. Zero means "no constraint", which is right while
    a person releases the job; `workers/plate_admission` refuses the *unattended*
    path outright rather than letting the same zero read as "fits everything".
    """
    asset_id = await an_asset(db_session)
    order_id = await a_paid_order(db_session, number="INTAKE-NOBOX", asset_id=asset_id, mesh={})

    await sweep.sweep()

    (job,) = await jobs_for(db_session, order_id)
    assert job.width_mm == Decimal(0)
    assert job.depth_mm == Decimal(0)
    assert job.height_mm == Decimal(0)


async def test_a_line_with_no_asset_still_becomes_a_job(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """The manual order desk, where the farm holds the model physically.

    `OrderLine.model_asset_id` is nullable for exactly this case. An empty
    `model_hash` here is honest — there is no geometry on file to digest — and it
    is the *guard* below that separates this from the failure it looks like.
    """
    order_id = await a_paid_order(db_session, number="INTAKE-3", asset_id=None)

    outcome = await sweep.sweep()

    assert outcome.jobs == 1
    (job,) = await jobs_for(db_session, order_id)
    assert job.model_asset_id is None
    assert job.model_hash == ""


async def test_a_line_with_an_asset_never_produces_a_job_without_its_digest(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """The silent-data-loss shape, refused.

    The state is constructed rather than natural — an asset with no digest should
    not exist — and that is the point of testing it: the guard is what makes the
    combination unreachable, so the test has to build it by hand to prove the
    refusal fires. The order keeps its status, so the next pass finds it again
    instead of it disappearing into a log line.
    """
    blank = await an_asset(db_session, sha256="")
    order_id = await a_paid_order(db_session, number="INTAKE-4", asset_id=blank)

    outcome = await sweep.sweep()

    assert outcome == type(outcome)(raised=0, jobs=0, failed=1)
    assert await jobs_for(db_session, order_id) == []
    order = await db_session.get(Order, order_id)
    assert order is not None
    assert order.status is OrderStatus.PAID


async def test_a_second_pass_does_not_raise_the_same_order_twice(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """Reconciling, so it must be safe to run for ever.

    The pass runs every thirty seconds against a table that keeps every order the
    farm has ever taken. A second job per tick would be a duplicate print, which
    is the expensive direction of this bug — filament and machine hours, not a
    row.
    """
    asset_id = await an_asset(db_session)
    order_id = await a_paid_order(db_session, number="INTAKE-5", asset_id=asset_id)

    first = await sweep.sweep()
    second = await sweep.sweep()

    assert first.jobs == 1
    assert second == type(second)(raised=0, jobs=0, failed=0)
    assert len(await jobs_for(db_session, order_id)) == 1


async def test_an_order_that_has_not_been_paid_is_left_alone(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """Production work is started by money, not by an order existing."""
    asset_id = await an_asset(db_session)
    order_id = await a_paid_order(
        db_session,
        number="INTAKE-6",
        asset_id=asset_id,
        status=OrderStatus.AWAITING_PAYMENT,
    )

    outcome = await sweep.sweep()

    assert outcome.jobs == 0
    assert await jobs_for(db_session, order_id) == []


async def test_a_paid_order_with_no_lines_is_not_advanced(
    db_session: AsyncSession, sweep: IntakeSweep
) -> None:
    """Nothing to print is a defect upstream, not a reason to move it along.

    Advancing it to `PREP` would put an empty order on an engineer's queue and
    lose the evidence that it was ever wrong. It stays `PAID` and visible.
    """
    order = Order(id=new_id(), number="INTAKE-7", status=OrderStatus.PAID)
    db_session.add(order)
    await db_session.flush()

    outcome = await sweep.sweep()

    assert outcome == type(outcome)(raised=0, jobs=0, failed=0)
    reloaded = await db_session.get(Order, order.id)
    assert reloaded is not None
    assert reloaded.status is OrderStatus.PAID
