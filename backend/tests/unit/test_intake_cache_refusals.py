"""Every way the automatic cache-hit path declines to *price* what it found.

`test_intake_cache_hit.py` is the half that proves the loop closes.
`test_intake_plate_selection.py` is the half that proves the farm attaches the
right plate or none. This file is the third: the plate is the right one, and the
money still cannot be worked out without inventing something.

That failure is the one worth the length. It is not "the order stayed in prep" —
it is a variance written from a number nobody took, which looks exactly like a
measured one for ever afterwards (CLAUDE.md §1), on the table ADR-0013 exists to
make trustworthy.

So: no pinned rates, rates that no longer rebuild to their own hash, a payload that
will not rebuild at all, a material gone from the catalogue, a plate with no
minutes, a multi-line order whose `line_total` was never a quote, and a sweep wired
without a plate library. Each leaves the job `PENDING` and the order in `PREP` —
which is exactly where every order went before
[#58](https://github.com/iritur/printorian/issues/58), so the fallback is the
behaviour #41 shipped rather than a new one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary
from printorian.contexts.ordering import OrderStatus
from printorian.contexts.ordering.models import Order
from printorian.contexts.production import JobStatus
from printorian.contexts.production.models import PrintJob
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.workers.intake import IntakeSweep
from tests.unit._intake_cache_support import (
    a_blind_sweep,
    a_cached_plate,
    a_material,
    a_paid_order,
    a_sweep,
    an_asset,
    rates_to_dict,
    some_rates,
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


@pytest.fixture
def blind_sweep(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> IntakeSweep:
    return a_blind_sweep(db_session, clock, bus)


# ------------------------------------------ the plate is right, the money is not


async def test_an_order_that_pinned_no_rates_is_not_repriced(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """Orders older than ADR-0020 have no snapshot, so their plate has no price.

    Repricing at today's rates would be exactly the thing the snapshot exists to
    prevent, and it would be invisible in the result.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="NORATES-1", asset_id=asset_id)
    order = await db_session.get(Order, order_id)
    assert order is not None
    order.rate_snapshot_id = None
    await db_session.flush()

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP


async def test_a_stored_snapshot_that_no_longer_rebuilds_to_its_own_id_is_refused(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """A row from an older schema, where `rates_from_dict` fills the gaps.

    `RateSnapshot` supplies today's default for a field the stored row does not
    carry, so the rebuilt object silently holds a number that was never in force.
    The id is the content hash of the values, so it stops matching — and that
    mismatch is the only thing standing between this path and a quietly re-rated
    order. Built by hand because the state cannot occur naturally yet, which is
    the point of testing it now rather than after a rate is added.
    """
    # A margin the farm does not charge today, so that dropping the key makes the
    # rebuild reach for a *different* number. Deleting a field whose stored value
    # happens to equal the current default proves nothing — the hash still
    # matches, and rightly so: those rates really were these rates.
    rates = some_rates(margin_percent=Decimal(42))
    payload = rates_to_dict(rates)
    del payload["margin_percent"]

    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(
        db_session, number="DRIFT-1", asset_id=asset_id, rates=rates, payload=payload
    )

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None


async def test_a_snapshot_payload_that_cannot_be_rebuilt_costs_one_order_not_the_pass(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """A stored rate the current `RateSnapshot` cannot parse, and what it may cost.

    The refusal itself is the same as every other one here. What this test is
    really about is the *blast radius*: `IntakeSweep.sweep` rescues only
    `PrintorianError` per order, so anything else — and `Decimal("")` raises
    `InvalidOperation`, which is not one — escapes the whole pass. The second
    order below is the assertion that matters: it has nothing wrong with it, and
    an unguarded rebuild would leave it jobless because a *different* order's
    snapshot was unreadable.
    """
    rates = some_rates()
    payload = rates_to_dict(rates)
    payload["margin_percent"] = "no longer a number"

    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    broken = await a_paid_order(
        db_session, number="BADPAY-1", asset_id=asset_id, rates=rates, payload=payload
    )
    other_asset = await an_asset(db_session, sha256="f" * 64)
    await a_cached_plate(library, model_hash="f" * 64)
    healthy = await a_paid_order(
        db_session,
        number="BADPAY-2",
        asset_id=other_asset,
        rates=some_rates(margin_percent=Decimal(41)),
    )

    outcome = await sweep.sweep()

    assert outcome.failed == 0
    assert (await the_job(db_session, broken)).status is JobStatus.PENDING
    assert await status_of(db_session, broken) is OrderStatus.PREP
    assert await the_variance(db_session, broken) is None
    # The order that was fine is still fine.
    assert (await the_job(db_session, healthy)).status is JobStatus.READY
    assert await status_of(db_session, healthy) is OrderStatus.QUEUED


async def test_a_multi_line_order_still_goes_to_prep(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """`line_total` on a multi-line order is a share, not a quote.

    `OrderingService.place` prices the order from its first line and apportions the
    total by quantity, so comparing a repriced line against that share would record
    a variance against a number nobody was ever quoted.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="MULTI-1", asset_id=asset_id, lines=2)

    outcome = await sweep.sweep()

    assert outcome.jobs == 2
    jobs = list(await db_session.scalars(select(PrintJob).where(PrintJob.order_id == order_id)))
    assert [job.status for job in jobs] == [JobStatus.PENDING, JobStatus.PENDING]
    assert await status_of(db_session, order_id) is OrderStatus.PREP


async def test_a_plate_with_no_minutes_is_not_priced_against(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """Numbers somebody has not finished typing in.

    A plate row may legitimately exist before its file and its figures do. Pricing
    against a zero would record a plate that prints in no time and costs nothing —
    a perfect estimate, invented.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, print_minutes=Decimal(0), grams=Decimal(0))
    order_id = await a_paid_order(db_session, number="EMPTY-1", asset_id=asset_id)

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP
    assert await the_variance(db_session, order_id) is None


async def test_a_material_that_left_the_catalogue_is_not_invented(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """No price per gram means no repriced plate — not a price of zero."""
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="NOMAT-1", asset_id=asset_id)

    await sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP


async def test_a_sweep_with_no_plate_library_behaves_as_it_did_before(
    db_session: AsyncSession, library: PlateLibrary, blind_sweep: IntakeSweep
) -> None:
    """The collaborator is optional, and the fallback is the old behaviour.

    Worth pinning: the failure mode of forgetting to wire it in `passes.py` is
    silent — every order simply goes to prep, exactly as before — so the fallback
    must at least be the *safe* one rather than an unpriced attach.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="BLIND-1", asset_id=asset_id)

    await blind_sweep.sweep()

    assert (await the_job(db_session, order_id)).status is JobStatus.PENDING
    assert await status_of(db_session, order_id) is OrderStatus.PREP
