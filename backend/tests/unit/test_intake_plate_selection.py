"""Which cached plate the unattended path may use, and every plate it refuses.

`find_unambiguous` and `CachedPlates._usable_plate` are the whole of the choosing:
one asks "is there exactly one plate for this configuration", the other asks "is
that plate one *this line* may have". Nothing else on the automatic path picks
anything, so a wrong answer here is a machine printing the wrong thing rather than
an order sitting in prep. `test_intake_cache_refusals.py` is the other half — the
plate is right and the *money* cannot be worked out honestly.

**This file exists because a reviewer deleted the guards and the suite stayed
green.** Removing `PreparedPlate.status == VALID` from `find_unambiguous`, and
replacing its plate_key comparison with `matches = list(rows)`, each left all
seventy selection tests passing while the sweep auto-attached a plate in the wrong
filament, or one an engineer had rejected. The layout tests are the round-two
finding: the first copy-count guard refused a line of three against an unknown
plate and then attached a *three-up plate to a line of one*, which is the normal
cache entry, not the exotic one.

Every test here leaves the job `PENDING` and the order in `PREP` — exactly where
every order went before [#58](https://github.com/iritur/printorian/issues/58).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary
from printorian.contexts.catalog.models import PlateStatus
from printorian.contexts.ordering import OrderStatus
from printorian.contexts.production import JobStatus
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.workers.intake import IntakeSweep
from tests.unit._intake_cache_support import (
    QUOTED_GRAMS,
    QUOTED_MINUTES,
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


# ------------------------------------------------- nothing to choose from


async def test_a_miss_still_routes_to_prep(db_session: AsyncSession, sweep: IntakeSweep) -> None:
    """The first order of a configuration. Unchanged, and it has to stay so."""
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    order_id = await a_paid_order(db_session, number="MISS-1", asset_id=asset_id)

    await sweep.sweep()

    job = await the_job(db_session, order_id)
    assert job.status is JobStatus.PENDING
    assert job.prepared_plate_id is None
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None


async def test_two_plates_for_one_configuration_are_not_chosen_between(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The same geometry sliced for two machines is a decision, not a lookup.

    Picking one would send a plate sliced for an X1C to a P1S, which prints and
    produces rubbish. The engineer is the one who knows.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, printer_profile="p1s-0.4-pla")
    await a_cached_plate(library, printer_profile="x1c-0.4-pla")
    order_id = await a_paid_order(db_session, number="AMBIG-1", asset_id=asset_id)

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP


# ------------------------------------------ something to choose, and it is wrong


async def test_a_plate_that_does_not_say_how_many_copies_it_holds_is_refused(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The state every plate in the table was in before `copies` existed.

    `PreparedPlate.copies` is nullable with no backfill, because nobody asked the
    engineers who sliced those plates and a `1` written in for them would be an
    invented number that happens to be the one that makes the common case attach
    (CLAUDE.md §1). NULL is "not measured", and not measured is not attachable.

    Delete the `plate.copies is None` half of the guard in
    `CachedPlates._usable_plate` and this order queues itself against a bed nobody
    counted.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, copies=None)
    order_id = await a_paid_order(db_session, number="QTY-0", asset_id=asset_id)

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None


async def test_a_two_up_plate_is_not_attached_to_a_line_of_one(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The direction the first guard did not cover, and the normal cache entry.

    A `PrintJob` is one plate holding a whole line's work, so the first order for
    two keychains leaves a **two-up** plate in the library — that is not the exotic
    case, it is the ordinary one. The repeat order for one then divides the plate's
    totals by a quantity of one, reprices against the whole bed rather than half of
    it, and lands *inside* ADR-0013's band anyway, because the band is a percentage
    of the line total and the doubled work is a fraction of it. Delete the guard and
    this exact order reprices at **3423.63 against 3000 — 14.12%, under the 15%
    band** — so it queues, the machine prints two, the customer is shipped one, and
    `EstimateVariance` records `within_tolerance=True`. (The review that found this
    measured 4.26% on a smaller pair, 20 min / 8 g against 10 min / 4 g; the point
    is that the band does not catch it at either size.)

    The plate below is deliberately *exactly* twice the quoted work, so the only
    thing standing between this order and `QUEUED` is the copy count.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(
        library, print_minutes=QUOTED_MINUTES * 2, grams=QUOTED_GRAMS * 2, copies=2
    )
    order_id = await a_paid_order(db_session, number="QTY-2", asset_id=asset_id, quantity=1)

    await sweep.sweep()

    job = await the_job(db_session, order_id)
    assert job.status is JobStatus.PENDING
    assert job.prepared_plate_id is None
    # Still the quoted work, not the plate's. `attach_plate` would have overwritten
    # this with the whole bed's 240 minutes, which is what the machine then runs.
    assert job.estimated_minutes == QUOTED_MINUTES
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None


async def test_a_one_up_plate_is_not_attached_to_a_line_of_three(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The other direction, which both under-prints *and* under-prices.

    Attach a one-up plate to a line of three and two things go wrong at once, both
    quietly: `attach_plate` overwrites the job's minutes and grams with the plate's,
    so the machine prints a third of what was sold; and the reprice divides the
    plate's totals by the quantity, so the line comes out at a third of the quoted
    work and sits comfortably *inside* the band. It dispatches.

    This used to be refused one step earlier, by `_may_attach_automatically`
    declining every line whose quantity was not one. That guard is gone — it was
    the wrong half of the comparison, and it made the two-up case above look safe —
    so what refuses here now is `1 != 3` in `CachedPlates._usable_plate`.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, copies=1)
    order_id = await a_paid_order(db_session, number="QTY-1", asset_id=asset_id, quantity=3)

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None


async def test_a_plate_sliced_in_another_material_is_not_attached(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """Same geometry, different filament — and the key is what says so.

    `find_unambiguous` cannot use the unique `plate_key` index, because an order
    carries no printer profile. It selects on `model_hash` and then rebuilds each
    candidate's key from *this order's* material and scale with *that plate's*
    profile and layout, keeping a row only when the two keys agree. Material is
    therefore enforced by a comparison rather than by a `WHERE`, which is exactly
    the kind of guard a later refactor drops without noticing.

    Replace that comprehension with `matches = list(rows)` and this order is
    auto-attached, dispatched and printed in PETG for a customer who bought PLA.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, material_code="petg-black")
    order_id = await a_paid_order(db_session, number="MAT-1", asset_id=asset_id)

    await sweep.sweep()

    job = await the_job(db_session, order_id)
    assert job.status is JobStatus.PENDING
    assert job.prepared_plate_id is None
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None


async def test_a_plate_an_engineer_retired_is_not_attached(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """`STALE` is what `invalidate_profile` leaves behind when a profile moves.

    A plate is never deleted — the jobs that printed from it have to stay
    explicable — so the row is still there, still the only one for its geometry,
    and still perfectly priceable. The single clause standing between it and a
    machine is `status == VALID` in `find_unambiguous`'s `WHERE`.

    Delete that clause and this order queues itself against a plate somebody has
    already said is wrong. `REJECTED` takes the same path; `STALE` is used here
    because it is the one the system produces by itself.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    plate = await a_cached_plate(library)
    await library.invalidate(plate.id, status=PlateStatus.STALE)
    order_id = await a_paid_order(db_session, number="STALE-1", asset_id=asset_id)

    await sweep.sweep()

    job = await the_job(db_session, order_id)
    assert job.status is JobStatus.PENDING
    assert job.prepared_plate_id is None
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None
