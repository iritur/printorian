"""The order, the plate and the rates the intake cache-hit tests are built on.

Shared by `test_intake_cache_hit.py`, which asserts that the automatic path
happens and that the number it writes down was measured;
`test_intake_plate_selection.py`, which asserts every plate it refuses to choose;
`test_intake_bed_admission.py`, which asserts everything about the *bed* that has
to match before a plate goes to a machine unwatched;
`test_intake_cache_refusals.py`, which asserts every way a chosen plate cannot be
priced honestly; and `test_intake_pass_wiring.py`, which asserts the pass the
worker runs is built to do any of it. One set of rows, because all five are about
the same order arriving in different states of the world — a second, subtly
different order builder is how they would drift into disagreeing about what a
cache hit even is.

Separate from `conftest.py` for the reason `tests/factories.py` gives: that file
is at the 400-line gate, and these are called from a test body rather than
requested by name.

The one exception is `expected_prepared_cost`, which is not a row at all. It is
this suite's *own* arithmetic, written out of `pricing.price` twice rather than
by calling `pricing.prepared_cost` — a test that calls the thing it is testing to
work out the expected answer asserts only that the function is deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary, PreparedPlateView, RecordPlate
from printorian.contexts.catalog.models import ModelAsset
from printorian.contexts.inventory.models import MaterialSpec
from printorian.contexts.ordering import OrderingService, OrderStatus
from printorian.contexts.ordering.models import Order, OrderLine, RateSnapshotRecord
from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
    rates_to_dict,
)
from printorian.contexts.production import ProductionService
from printorian.contexts.production.models import EstimateVariance, PrintJob
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import EntityId, new_id
from printorian.core.units import Duration, Mass
from printorian.workers.cached_plates import CachedPlates
from printorian.workers.intake import IntakeSweep

#: The digest the plate cache is keyed on.
CUBE_SHA = "c" * 64

#: `model_assets.last_used_at` is NOT NULL. A fixed instant: nothing asserts on
#: it, and a wall clock in a fixture is a test that fails at midnight.
SOME_TIME = datetime(2026, 1, 1, tzinfo=UTC)

MATERIAL = "pla-white"
PRICE_PER_GRAM = Decimal("2.40")

#: ADR-0013's band, as configuration reaches the sweep. Generous enough that the
#: happy-path plates below sit inside it and the held one obviously does not.
TOLERANCE = Decimal("0.15")

#: What the mesh heuristic guessed at quote time, per unit.
QUOTED_MINUTES = Decimal(120)
QUOTED_GRAMS = Decimal(50)

#: The geometry the line was priced from, as `_pricing_spec` writes it onto the
#: order. Present rather than `{}` because the unattended path **refuses a line
#: whose part was never measured**: the job's box is what `fleet.can_take` judges
#: a machine against, and a zero there reads as "fits everything". A line built
#: without this is a line that goes to prep, which is what
#: `test_intake_bed_admission.py` asserts on purpose.
CUBE_MESH: dict[str, object] = {
    "measured": True,
    "bounding_box_mm": {"x": "40", "y": "40", "z": "40"},
}

#: What the slicer actually found for the whole plate. Longer and heavier than
#: the guess on purpose: a plate that came in *under* the estimate is within
#: tolerance whatever the arithmetic does, so it would prove nothing about the
#: cost having been derived at all.
PLATE_MINUTES = Decimal(150)
PLATE_GRAMS = Decimal("62.5")


def some_rates(**overrides: Decimal) -> RateSnapshot:
    """The rates an order is pinned to. Overridable so a test can move them."""
    return RateSnapshot(**overrides)


def quoted_spec(quantity: int = 1) -> PriceSpec:
    """The pricing question the line was quoted from, as this file builds it."""
    return PriceSpec(
        estimate=PrintEstimate(
            print_time=Duration(QUOTED_MINUTES), material_mass=Mass(QUOTED_GRAMS)
        ),
        material=MaterialPrice(spec_code=MATERIAL, price_per_gram=PRICE_PER_GRAM),
        quantity=quantity,
        colors=("white",),
        scale=Decimal(1),
        include_shipping=False,
    )


def expected_prepared_cost(
    rates: RateSnapshot,
    *,
    line_total: Decimal,
    plate_minutes: Decimal = PLATE_MINUTES,
    plate_grams: Decimal = PLATE_GRAMS,
    quantity: int = 1,
) -> Decimal:
    """What the plate's work costs, computed here rather than imported.

    Deliberately written out of `pricing.price` twice rather than by calling
    `pricing.prepared_cost`: a test that calls the thing it is testing to work out
    the expected answer asserts only that the function is deterministic.
    """
    quoted = quoted_spec(quantity)
    units = Decimal(quantity)
    prepared = quoted.with_changes(
        estimate=PrintEstimate(
            print_time=Duration(plate_minutes / units),
            material_mass=Mass(plate_grams / units),
        )
    )
    change = price(prepared, rates).total.amount - price(quoted, rates).total.amount
    return line_total + change


def a_sweep(
    db: AsyncSession, clock: FixedClock, bus: EventBus, library: PlateLibrary
) -> IntakeSweep:
    """The sweep as `workers/passes.py` builds it: able to close the loop itself.

    A builder rather than a fixture, so each test file declares the fixture it
    actually uses and pytest is never asked to resolve one across an import.
    """
    return IntakeSweep(
        db,
        ProductionService(db, clock, bus),
        OrderingService(db, clock, bus),
        CachedPlates(db, library),
        tolerance=TOLERANCE,
    )


def a_blind_sweep(db: AsyncSession, clock: FixedClock, bus: EventBus) -> IntakeSweep:
    """The sweep as it was before #58: no plate library, so no automatic path."""
    return IntakeSweep(
        db,
        ProductionService(db, clock, bus),
        OrderingService(db, clock, bus),
    )


async def a_material(db: AsyncSession) -> None:
    """The catalogue row the reprice reads a price per gram from."""
    db.add(
        MaterialSpec(
            code=MATERIAL,
            name="PLA White",
            family="PLA",
            sell_price_per_gram=PRICE_PER_GRAM,
        )
    )
    await db.flush()


async def an_asset(db: AsyncSession, *, sha256: str = CUBE_SHA) -> EntityId:
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
    rates: RateSnapshot | None = None,
    payload: dict[str, object] | None = None,
    line_total: Decimal = Decimal(3000),
    quantity: int = 1,
    lines: int = 1,
    colors: list[str] | None = None,
    mesh: dict[str, object] | None = None,
    engine_version: str = "1.0.0",
) -> EntityId:
    """An order sitting where `payments` leaves it, with its rates pinned.

    ``payload`` overrides what is *stored* for the snapshot while leaving the id
    alone, which is the only way to build the row ADR-0020's guard is about.
    """
    rates = rates or some_rates()
    snapshot_id = rates.snapshot_id
    if await db.get(RateSnapshotRecord, snapshot_id) is None:
        db.add(
            RateSnapshotRecord(
                id=snapshot_id,
                payload=payload if payload is not None else rates_to_dict(rates),
                engine_version=engine_version,
            )
        )
        await db.flush()

    order = Order(
        id=new_id(),
        number=number,
        status=OrderStatus.PAID,
        rate_snapshot_id=snapshot_id,
        engine_version=engine_version,
    )
    db.add(order)
    await db.flush()
    for index in range(lines):
        db.add(
            OrderLine(
                order_id=order.id,
                model_name="cube.stl",
                model_asset_id=asset_id,
                material_code=MATERIAL,
                quantity=quantity,
                scale=Decimal(1),
                colors=list(colors) if colors is not None else ["white"],
                mesh=dict(mesh) if mesh is not None else dict(CUBE_MESH),
                estimated_minutes=QUOTED_MINUTES,
                estimated_grams=QUOTED_GRAMS,
                line_total=line_total if index == 0 else Decimal(1000),
            )
        )
    await db.flush()
    return order.id


async def a_cached_plate(
    library: PlateLibrary,
    *,
    printer_profile: str = "p1s-0.4-pla",
    print_minutes: Decimal = PLATE_MINUTES,
    grams: Decimal = PLATE_GRAMS,
    model_hash: str = CUBE_SHA,
    material_code: str = MATERIAL,
    copies: int | None = 1,
    slots: int = 1,
    has_content: bool = True,
) -> PreparedPlateView:
    """What an engineer's slicing left behind for the *previous* order.

    ``copies`` defaults to one because that is what the orders in these files are
    for, not because one is a safe assumption anywhere else — `PreparedPlate.copies`
    is nullable precisely so an unrecorded layout stays unrecorded, and
    `test_intake_plate_selection.py` passes `None` to say so. The plate view is
    returned so a test can go on to invalidate the row it just made.

    ``slots`` is how many AMS slots the bed calls for; the grams are split evenly
    across them because nothing here asserts on the split, only on the count —
    which is the one thing about the filament set a plate records.
    ``has_content=False`` is the plate that is numbers with no file behind it, a
    legitimate row and one the unattended path must not send to a machine.
    """
    per_slot = grams / Decimal(slots)
    return await library.record(
        RecordPlate(
            model_hash=model_hash,
            model_name="cube.stl",
            scale=Decimal(1),
            material_code=material_code,
            printer_profile=printer_profile,
            copies=copies,
            print_minutes=print_minutes,
            filament_grams={str(slot): per_slot for slot in range(slots)},
            filename="cube.3mf",
            content_sha256="d" * 64 if has_content else None,
            storage_path="plates/cube.3mf" if has_content else None,
            size_bytes=1024 if has_content else None,
        )
    )


async def the_job(db: AsyncSession, order_id: EntityId) -> PrintJob:
    (job,) = list(await db.scalars(select(PrintJob).where(PrintJob.order_id == order_id)))
    return job


async def the_variance(db: AsyncSession, order_id: EntityId) -> EstimateVariance | None:
    return await db.scalar(select(EstimateVariance).where(EstimateVariance.order_id == order_id))


async def status_of(db: AsyncSession, order_id: EntityId) -> OrderStatus:
    order = await db.get(Order, order_id)
    assert order is not None
    return order.status
