"""What the farm must know about the bed before it prints one unattended.

`test_intake_plate_selection.py` is "is there a plate, and is it *this
configuration's* plate" — the key, the material, the scale, the status, the copy
count. This file is the other half of the same question and the one three reviews
kept finding one dimension at a time: **the key identifies the order's side of the
configuration and says almost nothing about the bed.** What filaments it calls
for, how big it is, whether there are any bytes to send — none of that is in
`plate_key`, and every one of them can send a machine work nobody checked.

Each test below deletes one clause of `workers/plate_admission.admits` and gets a
`QUEUED` order out of it. That is the shape of the failure in all four cases: not
an error, not a hold, but an order that reads as correct, an
`EstimateVariance` that reads as measured, and a plate on a machine that was never
compared to what was ordered.

Every test here leaves the job `PENDING` and the order in `PREP` — where every
order went before [#58](https://github.com/iritur/printorian/issues/58), and where
a person can see it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary
from printorian.contexts.ordering import OrderStatus
from printorian.contexts.production import JobStatus
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.workers.intake import IntakeSweep
from tests.unit._intake_cache_support import (
    PLATE_GRAMS,
    PLATE_MINUTES,
    a_cached_plate,
    a_material,
    a_paid_order,
    a_sweep,
    an_asset,
    status_of,
    the_job,
    the_variance,
)


@pytest.fixture
def library(db_session: AsyncSession, clock: FixedClock) -> PlateLibrary:
    return PlateLibrary(db_session, clock)


@pytest.fixture
def sweep(
    db_session: AsyncSession, clock: FixedClock, bus: EventBus, library: PlateLibrary
) -> IntakeSweep:
    return a_sweep(db_session, clock, bus, library)


async def assert_went_to_prep(db: AsyncSession, order_id: EntityId) -> None:
    """The one outcome every refusal on this path has to produce.

    Asserted as four facts rather than one, because they can come apart: a job can
    be left `PENDING` with a plate already attached to it, and a variance can be
    written for an attach that was then declined. Nothing about this order may have
    been decided.
    """
    job = await the_job(db, order_id)
    assert job.status is JobStatus.PENDING
    assert job.prepared_plate_id is None
    assert await status_of(db, order_id) is OrderStatus.PREP
    assert await the_variance(db, order_id) is None


# ------------------------------------------------------------ the filament set


async def test_a_two_filament_plate_is_not_attached_to_a_one_colour_line(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """Round three's finding, and the one that survives every key term there is.

    Colour is in no part of `plate_key`, and `line.material_code` is not the
    filament set: `_pricing_spec._material_price` picks the **dearest** of the
    chosen specs and writes that one code onto the line. So an order for white and
    black leaves behind a two-filament plate keyed on `"pla-white"`, and the next
    white-only order matches it on geometry, scale, material, profile and layout.

    Reproduced end to end before the guard existed: the job went `READY` with
    `colors=['white']` while the plate's slots were `{'0': …, '1': …}`, the order
    went `QUEUED`, and `within_tolerance` was `True` — the purge charge cancels,
    because both sides of the reprice use the order's own colours. The machine is
    then asked to load one filament and handed a plate that needs two.

    Delete the slot comparison in `plate_admission.admits` and this queues.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, slots=2)
    order_id = await a_paid_order(db_session, number="COLOUR-1", asset_id=asset_id)

    await sweep.sweep()

    await assert_went_to_prep(db_session, order_id)


async def test_a_one_filament_plate_is_not_attached_to_a_two_colour_line(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The mirror, which is the commercially worse one.

    The customer paid for an AMS purge that never happens and is shipped a
    single-colour part against a two-colour order — and the variance records the
    estimate as accurate, because the purge is on both sides of the difference.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, slots=1)
    order_id = await a_paid_order(
        db_session, number="COLOUR-2", asset_id=asset_id, colors=["white", "black"]
    )

    await sweep.sweep()

    await assert_went_to_prep(db_session, order_id)


async def test_two_slots_of_one_colour_are_one_filament(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The rule `core.colors` exists to hold, asserted on this path too.

    A line asking for white twice is a **single-filament** line: purge is spent
    flushing the nozzle between *different* filaments, and two slots of white flush
    nothing. Comparing `len(line.colors)` instead of `distinct_colors(...)` would
    refuse this perfectly good one-up plate — a cache miss and an engineer for a
    plate that is exactly right — which is the failure `core.colors` was written
    about, in a fourth place.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, slots=1)
    order_id = await a_paid_order(
        db_session, number="COLOUR-3", asset_id=asset_id, colors=["white", "White"]
    )

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.READY
    assert await status_of(db_session, order_id) is OrderStatus.QUEUED


async def test_a_line_that_records_no_colours_at_all_is_refused(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """Zero colours is "the configurator recorded nothing", not "no filament".

    `_quoted_spec` prices such a line as `("default",)`, which is a stand-in and
    not a measurement, and there is no count to compare a plate's slots against.
    CLAUDE.md §1: not measured is not a number.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, slots=1)
    order_id = await a_paid_order(db_session, number="COLOUR-4", asset_id=asset_id, colors=[])

    await sweep.sweep()

    await assert_went_to_prep(db_session, order_id)


# ------------------------------------------------------------- the bed's extent


async def test_a_multi_up_plate_is_not_attached_because_the_bed_is_not_measured(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The copy count agrees and the plate is still refused, deliberately.

    Recording `PreparedPlate.copies` made a line of three attachable to a three-up
    plate, and that is where the second review left it. What nothing records is the
    bed's own **footprint**: `copies` says how many parts are on it, nothing says
    how they are arranged, and the only geometry the planner ever sees is the
    job's — one part's box, from `intake._job_for`. `fleet.can_take`'s single
    geometric test then judges a three-up bed by the footprint of one part, and a
    machine that cannot hold the plate is eligible for it.

    So this is a narrowing, taken knowingly: the money is right, the count is
    right, and the farm still does not know how big the thing is. Recording the
    plate's bed extent — two columns and a console field, exactly the shape
    `copies` took — is what lifts it, after which the check becomes "the recorded
    footprint fits the machine" rather than "there is only one part".
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, print_minutes=PLATE_MINUTES * 3, grams=PLATE_GRAMS * 3, copies=3)
    order_id = await a_paid_order(db_session, number="BED-1", asset_id=asset_id, quantity=3)

    await sweep.sweep()

    await assert_went_to_prep(db_session, order_id)


async def test_a_line_whose_part_was_never_measured_is_refused(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """A zero box reads as "fits every machine", and on this path nobody checks.

    `_dimensions` leaves the job's width, depth and height at zero for geometry
    nobody measured, which the planner reads as "no constraint" — right while a
    person releases the job, and CLAUDE.md §1's invented number when nothing does.
    `fleet.can_take`'s geometric test is then vacuous and every printer in the farm
    is eligible for a part of unknown size.

    It costs real orders today: `CheckoutPage.tsx` sends no `mesh`, so every line
    the web checkout places lands here. That refusal is visible — the order is in
    prep — and a plate on a machine too small for it is not.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="BED-2", asset_id=asset_id, mesh={})

    await sweep.sweep()

    await assert_went_to_prep(db_session, order_id)


# ------------------------------------------------------------ bytes to send


async def test_a_plate_that_is_numbers_with_no_file_is_not_attached(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The refusal that would otherwise land in the one state nothing recovers from.

    A plate row may legitimately be numbers an engineer typed with no file behind
    them — `POST /jobs/{id}/plate` is that route — and `planning.plate_to_send`
    does check for the bytes. It checks too late, and it fails in the direction
    that never comes back: job `READY`, order `QUEUED`, a machine assigned,
    `DISPATCH_NO_PLATE`, and `_return_to_queue` puts the job back — for ever. A
    paid order then reads as queued, never prints, and never returns to `PREP`,
    which is where every other refusal on this path deliberately lands it. The
    `EstimateVariance` saying `within_tolerance=True` has already been written.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, has_content=False)
    order_id = await a_paid_order(db_session, number="BYTES-1", asset_id=asset_id)

    await sweep.sweep()

    await assert_went_to_prep(db_session, order_id)
