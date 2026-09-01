"""A paid order whose configuration is already sliced, reaching a printer alone.

ROADMAP Phase 4's exit criterion in full: *payment to a machine starting the job
with no human action*. `test_intake_sweep.py` covers the half that makes the jobs;
this is the half that skips prep, and the half where money is involved. Every way
the automatic path declines to act is in `test_intake_cache_refusals.py`.

The assertions here divide in three, and each part matters.

**That it happens at all** — the order reaches `QUEUED` carrying the plate the
farm already had, and nobody clicked.

**That the number written down was measured.** `attach_prepared_plate` records an
`EstimateVariance` whose `prepared_cost` is `NOT NULL`, and the reason
[#41](https://github.com/iritur/printorian/issues/41) stopped short of this is
that nothing priced a plate: a zero there, or the quote copied across, records
"the estimate was perfect" for a variance nobody took — CLAUDE.md §1, in the
flattering direction, on the table ADR-0013 exists to make trustworthy. So the
cost is asserted against a figure `_intake_cache_support` computes for itself out
of the pricing engine, and then again by *moving* the plate and the order's pinned
rates and watching it follow.

**That it was written down where only a financial reader can reach it.** The same
two figures went into the job's journal as well as onto the `EstimateVariance`
row, and a `JobEvent`'s `details` is served by `GET /jobs/{job_id}` under
`VIEW_PRODUCTION` alone. That was invisible while the console — which sends
neither cost — was the only caller and the field read `"0"`; this sweep is what
fills it, so the last test here is the one that keeps it empty.

Both of the shapes #41 refused were then applied as mutations and run. Passing
`Decimal(0)` failed four of the five tests here; passing `line.line_total` failed
the same four. Returning a fresh `RateSnapshot()` from `CachedPlates._rates_for`
instead of the pinned one failed
`test_the_orders_own_rates_are_used_and_not_todays` and nothing else — which is
what that test is for, and why it is not folded into the one above it.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import PlateLibrary
from printorian.contexts.ordering import OrderStatus
from printorian.contexts.production import JobStatus
from printorian.contexts.production.models import JobEvent, PrintJob
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.workers.intake import IntakeSweep
from tests.unit._intake_cache_support import (
    PLATE_GRAMS,
    PLATE_MINUTES,
    QUOTED_MINUTES,
    TOLERANCE,
    a_cached_plate,
    a_material,
    a_paid_order,
    a_sweep,
    an_asset,
    expected_prepared_cost,
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


def _number(value: object) -> Decimal | None:
    """The value as a figure, or `None` when it is a plate id or a filename."""
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


# ------------------------------------------------------- the hit reaches QUEUED


async def test_a_cache_hit_reaches_queued_with_no_human_action(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """Phase 4's exit criterion, for the half that used to need a click."""
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="HIT-1", asset_id=asset_id)

    outcome = await sweep.sweep()

    assert outcome == type(outcome)(raised=1, jobs=1, failed=0)
    job = await the_job(db_session, order_id)
    assert job.status is JobStatus.READY
    assert job.prepared_plate_id is not None
    assert job.plate_filename == "cube.3mf"
    # The plate is the better truth, so the job schedules against slicer numbers.
    assert job.estimated_minutes == PLATE_MINUTES
    assert job.grams_required == PLATE_GRAMS
    assert await status_of(db_session, order_id) is OrderStatus.QUEUED
    # Nothing is waiting for a person: the prep queue is where the click was.
    assert (
        await db_session.scalar(select(PrintJob).where(PrintJob.status == JobStatus.PENDING))
        is None
    )


# ------------------------------------------- the prepared cost was actually taken


async def test_the_variance_cost_is_derived_from_the_plate_under_the_pinned_rates(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The whole of why #41 stopped here, asserted as a number.

    The expected figure is computed in this file from `pricing.price`, so a
    `prepared_cost` of zero, of `line_total`, or of anything else convenient fails
    it.
    """
    rates = some_rates()
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="HIT-2", asset_id=asset_id, rates=rates)

    await sweep.sweep()

    variance = await the_variance(db_session, order_id)
    assert variance is not None
    assert variance.quoted_cost == Decimal(3000)
    assert variance.prepared_cost == expected_prepared_cost(rates, line_total=Decimal(3000))
    # Stated separately, because these two are the shapes #41 refused to write.
    assert variance.prepared_cost != Decimal(0)
    assert variance.prepared_cost != variance.quoted_cost
    assert variance.within_tolerance is True
    # The manufacturing numbers behind the money, for the Phase 6 calibration.
    assert variance.estimated_minutes == QUOTED_MINUTES
    assert variance.prepared_minutes == PLATE_MINUTES
    assert variance.prepared_grams == PLATE_GRAMS


async def test_a_longer_plate_costs_more_than_a_shorter_one(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The cost follows the plate, which no constant and no copied quote does."""
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library, print_minutes=Decimal(130))
    cheap_order = await a_paid_order(db_session, number="HIT-3", asset_id=asset_id)

    other_asset = await an_asset(db_session, sha256="e" * 64)
    await a_cached_plate(library, model_hash="e" * 64, print_minutes=Decimal(300))
    dear_order = await a_paid_order(db_session, number="HIT-4", asset_id=other_asset)

    await sweep.sweep()

    cheap = await the_variance(db_session, cheap_order)
    dear = await the_variance(db_session, dear_order)
    assert cheap is not None and dear is not None
    assert cheap.quoted_cost == dear.quoted_cost
    assert dear.prepared_cost > cheap.prepared_cost


async def test_the_orders_own_rates_are_used_and_not_todays(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """ADR-0020, which is the reason this reprice is allowed to exist at all.

    Two identical orders, identical plates, different *pinned* snapshots. If the
    reprice reached for a live rate table instead, both would come out the same.
    """
    standard = some_rates()
    expensive = some_rates(labor_rate_per_hour=Decimal(2400))
    assert standard.snapshot_id != expensive.snapshot_id

    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    cheap_order = await a_paid_order(db_session, number="RATE-1", asset_id=asset_id, rates=standard)
    dear_order = await a_paid_order(db_session, number="RATE-2", asset_id=asset_id, rates=expensive)

    await sweep.sweep()

    cheap = await the_variance(db_session, cheap_order)
    dear = await the_variance(db_session, dear_order)
    assert cheap is not None and dear is not None
    assert cheap.prepared_cost == expected_prepared_cost(standard, line_total=Decimal(3000))
    assert dear.prepared_cost == expected_prepared_cost(expensive, line_total=Decimal(3000))
    assert dear.prepared_cost > cheap.prepared_cost


# --------------------------------------------------- the branch that costs money


async def test_a_plate_over_the_band_is_held_rather_than_queued(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """ADR-0013's stop, reached without an engineer having been involved.

    This is the expensive direction of a wrong answer: a job that should have
    stopped for a person instead goes to a machine and prints work the farm is
    underpaid for. The order lands `PRICE_REVIEW`, which is a transition straight
    from `PAID` — prep never happened, and recording that it had would be a lie
    about who touched the order.
    """
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    # Four times the quoted print time, against a 15% band.
    await a_cached_plate(library, print_minutes=Decimal(480), grams=Decimal(200))
    order_id = await a_paid_order(db_session, number="HOLD-1", asset_id=asset_id)

    await sweep.sweep()

    job = await the_job(db_session, order_id)
    assert job.status is JobStatus.ON_HOLD
    # The plate is still attached: the money is in question, not the plate.
    assert job.prepared_plate_id is not None
    assert await status_of(db_session, order_id) is OrderStatus.PRICE_REVIEW

    variance = await the_variance(db_session, order_id)
    assert variance is not None
    assert variance.within_tolerance is False
    assert variance.prepared_cost > variance.quoted_cost * (1 + TOLERANCE)


async def test_the_sweeps_journal_entry_carries_no_money(
    db_session: AsyncSession, library: PlateLibrary, sweep: IntakeSweep
) -> None:
    """The pair goes on the variance row and nowhere a production read can see it.

    `EstimateVariance` is behind `VIEW_FINANCIALS`; a `JobEvent`'s `details` is
    not — it rides out on `JobView.events`, which `GET /jobs/{job_id}` serves to
    anyone with `VIEW_PRODUCTION`. `attach_plate` wrote both costs into both
    places, and until this branch the second copy read `"0"` because the console
    was the only caller and does not send the two query parameters. This sweep is
    what fills it with the order's real total, so this is the test that has to
    exist alongside the one above asserting the variance row *does* carry it.

    The permission boundary itself is asserted end-to-end in
    `tests/api/test_variance_api.py`; this one guards the write.
    """
    rates = some_rates()
    await a_material(db_session)
    asset_id = await an_asset(db_session)
    await a_cached_plate(library)
    order_id = await a_paid_order(db_session, number="HIT-5", asset_id=asset_id, rates=rates)

    await sweep.sweep()

    job = await the_job(db_session, order_id)
    variance = await the_variance(db_session, order_id)
    assert variance is not None
    events = list(await db_session.scalars(select(JobEvent).where(JobEvent.job_id == job.id)))
    [attached] = [event for event in events if event.reason == "plate.attached"]
    assert attached.details["overrun_ratio"] != "0"
    # Not "the keys are absent" but "the figures are not there under any name":
    # what leaks is the number, and a rename would keep the leak. Compared as
    # `Decimal` rather than as text, because `"3000"` and `"3000.00"` are the
    # same rouble figure and a string test would pass on the wrong one.
    written = {_number(value) for event in events for value in event.details.values()} - {None}
    assert variance.quoted_cost not in written
    assert variance.prepared_cost not in written
