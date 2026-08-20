"""The packing bench.

The claims worth pinning are the ones a screen full of confident numbers would
otherwise hide:

* the cheapest box that fits wins, and nothing is recommended for a batch nobody
  measured — a zero fits inside everything, which is the trap;
* consumption is written when the packer names the box, not when the parcel
  closes, so an abandoned parcel still shows as having eaten one;
* the clock stops when the packer stops, so a parcel parked on an unpaid invoice
  did not take fourteen hours;
* a short parcel stops where it stands rather than shipping with a flag on it;
* the board is ordered by the van, because everything in one pickup is due at the
  same instant however long ago it was inspected.

The geometry those rest on is pinned separately, in `test_packaging_geometry.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.packaging import (
    ChooseTara,
    CreatePackStep,
    CreatePackTask,
    HoldParcel,
    HoldReason,
    PackagingService,
    PackingCatalogue,
    PackStatus,
    PackUse,
    PublishInstruction,
    ReportDiscrepancy,
    board_columns,
    enclosures,
    metrics,
    recommend,
    tara_rows,
)
from printorian.core.clock import FixedClock
from printorian.core.errors import DomainRuleViolationError
from printorian.core.events import EventBus
from tests.unit._packaging_support import (
    a_packer,
    a_parcel,
    a_shelf,
    an_instruction,
    an_order,
    mm,
)

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


# --------------------------------------------------------------- the choice


async def test_the_cheapest_box_that_fits_wins(db_session: AsyncSession) -> None:
    """Cheapest, not tightest: the question is about money."""
    await a_shelf(db_session)

    chosen = recommend(await enclosures(db_session), mm(190, 140, 70))

    assert chosen is not None
    assert chosen.code == "box-a"


async def test_a_batch_nothing_fits_gets_no_recommendation(db_session: AsyncSession) -> None:
    """A real answer, not a shrug.

    Naming the largest box for something that does not go in it would send a
    packer to the bench with a decision the screen pretended to have made.
    """
    await a_shelf(db_session)

    assert recommend(await enclosures(db_session), mm(900, 900, 900)) is None


async def test_an_unmeasured_batch_gets_no_recommendation_either(
    db_session: AsyncSession,
) -> None:
    """A zero fits inside everything, and that is the trap.

    An order priced from an estimate rather than from geometry reaches the bench
    with no bounding box, and the naive answer is the cheapest bag in the
    catalogue offered with total confidence. Silence is the honest output.
    """
    await a_shelf(db_session)

    assert recommend(await enclosures(db_session), mm(0, 0, 0)) is None


async def test_a_roll_of_film_is_never_offered_as_a_box(db_session: AsyncSession) -> None:
    await a_shelf(db_session)

    assert [tara.code for tara in await enclosures(db_session)] == ["box-a", "box-b"]


# ---------------------------------------------------------------- the clock


async def test_the_clock_stops_when_the_packer_stops(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A parcel parked on an unpaid invoice did not take fourteen hours."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))

    clock.advance(timedelta(minutes=6))
    await service.hold(parcel.id, HoldParcel(reason=HoldReason.INVOICE_UNPAID))
    clock.advance(timedelta(hours=14))

    await service.release(parcel.id)
    resumed = await service.start(parcel.id, await a_packer(db_session))
    assert resumed.elapsed_minutes == Decimal(6)


async def test_a_step_costs_the_time_since_the_previous_one(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The parts have to add up to the whole, or the norms are unfalsifiable."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))

    clock.advance(timedelta(minutes=3))
    await service.tick(parcel.id, 1)
    clock.advance(timedelta(minutes=5))
    after = await service.tick(parcel.id, 2)

    ticked = {step.position: step.actual_minutes for step in after.steps}
    assert ticked[1] == Decimal(3)
    assert ticked[2] == Decimal(5)
    assert after.elapsed_minutes == Decimal(8)


async def test_the_last_step_closes_the_parcel(db_session: AsyncSession, clock: FixedClock) -> None:
    """Otherwise sealed parcels sit on a bench that still calls them work in hand."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))

    for position in (1, 2, 3, 4):
        clock.advance(timedelta(minutes=2))
        after = await service.tick(parcel.id, position)

    assert after.status is PackStatus.READY
    assert after.finished_at is not None


async def test_the_norm_is_the_instruction_s_own_total(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Not multiplied by the piece count: packing ten brackets is one pack."""
    _, parcel = await a_parcel(db_session, clock)

    assert parcel.norm_minutes == Decimal(9)
    assert parcel.instruction_version == "2.1"


async def test_republishing_does_not_rewrite_a_parcel_in_hand(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A packer working to 2.1 stays measured against 2.1."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))

    await PackingCatalogue(db_session).publish(
        PublishInstruction(
            version="2.2",
            steps=[CreatePackStep(position=1, title="Всё сразу", norm_minutes=Decimal(1))],
        )
    )

    unchanged = await service.tick(parcel.id, 1)
    assert unchanged.instruction_version == "2.1"
    assert len(unchanged.steps) == 4


async def test_a_published_version_cannot_be_quietly_replaced(db_session: AsyncSession) -> None:
    """Parcels claim a version; the claim has to stay true."""
    await an_instruction(db_session)

    with pytest.raises(DomainRuleViolationError):
        await PackingCatalogue(db_session).publish(
            PublishInstruction(
                version="2.1",
                steps=[CreatePackStep(position=1, title="Другое", norm_minutes=Decimal(1))],
            )
        )


# ------------------------------------------------------------ what it costs


async def test_the_box_is_consumed_when_the_packer_names_it(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A parcel abandoned halfway has still eaten the box."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))
    shelf = {tara.code: tara for tara in await enclosures(db_session)}

    after = await service.choose_tara(parcel.id, ChooseTara(tara_id=shelf["box-a"].id, extras={}))

    assert after.tara_name == "Коробка A"
    assert after.packaging_cost == Decimal("62.00")
    assert shelf["box-a"].stock == Decimal(9)


async def test_changing_the_box_replaces_the_ledger_rather_than_adding_to_it(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Otherwise the parcel is billed for two enclosures it never had."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))
    shelf = {tara.code: tara for tara in await enclosures(db_session)}

    await service.choose_tara(parcel.id, ChooseTara(tara_id=shelf["box-a"].id, extras={}))
    after = await service.choose_tara(parcel.id, ChooseTara(tara_id=shelf["box-b"].id, extras={}))

    used = list(await db_session.scalars(select(PackUse).where(PackUse.task_id == parcel.id)))
    assert len(used) == 1
    assert after.packaging_cost == Decimal("94.00")


async def test_the_tara_table_measures_consumption_rather_than_asking(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """«Хватит на» is a statement about what the post did, not about a typed field."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))
    shelf = {tara.code: tara for tara in await enclosures(db_session)}
    await service.choose_tara(parcel.id, ChooseTara(tara_id=shelf["box-a"].id, extras={}))
    for position in (1, 2, 3, 4):
        clock.advance(timedelta(minutes=2))
        await service.tick(parcel.id, position)

    rows = {row.code: row for row in await tara_rows(db_session, now=clock.now())}

    assert rows["box-a"].used_per_month == Decimal("1.0")
    assert rows["box-a"].months_left == Decimal("9.0")
    # Nothing has consumed a roll, so it has no measured rate — which is not the
    # same as lasting for ever.
    assert rows["wrap-roll"].months_left is None


# ------------------------------------------------------------- what goes wrong


async def test_a_short_parcel_stops_where_it_stands(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A short parcel that stayed packable is a short parcel that ships."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))

    after = await service.report_discrepancy(
        parcel.id, ReportDiscrepancy(discrepancy_code="discrepancy.short_count", note="9 из 10")
    )

    assert after.status is PackStatus.HELD
    assert after.hold_reason is HoldReason.ITEM_MISSING
    assert after.discrepancy_code == "discrepancy.short_count"


async def test_a_short_parcel_stays_counted_in_the_month_it_happened(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The reason the stamp is its own column.

    `updated_at` moves whenever anything on the row does, so a parcel found short
    in June and shipped in August would be an August discrepancy — and the
    days-without-a-short-parcel figure would reset on a touch nobody could connect
    to it.
    """
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))
    await service.report_discrepancy(
        parcel.id, ReportDiscrepancy(discrepancy_code="discrepancy.short_count")
    )
    found_at = clock.now()

    # The parcel is cleared and shipped two months later. It is still touched.
    clock.advance(timedelta(days=60))
    await service.release(parcel.id)
    await service.start(parcel.id, await a_packer(db_session))
    after = await service.ready(parcel.id)

    assert after.discrepancy_at == found_at
    # Counted in the window that contains June, not the one that contains August.
    assert (await metrics(db_session, now=found_at, days=1)).discrepancies == 1
    assert (await metrics(db_session, now=clock.now(), days=1)).discrepancies == 0


async def test_a_cleared_hold_returns_to_the_queue_not_to_a_packer(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Whoever cleared the invoice is rarely the person who will pack it."""
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))
    await service.hold(parcel.id, HoldParcel(reason=HoldReason.WAYBILL_MISSING))

    after = await service.release(parcel.id)

    assert after.status is PackStatus.CHECKED
    assert after.hold_reason is None


async def test_a_shipped_parcel_cannot_move_again(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))
    await service.ready(parcel.id)
    await service.ship(parcel.id)

    with pytest.raises(DomainRuleViolationError):
        await service.hold(parcel.id, HoldParcel(reason=HoldReason.INVOICE_UNPAID))


# ------------------------------------------------------------------ the board


async def test_the_board_is_ordered_by_the_van(db_session: AsyncSession, clock: FixedClock) -> None:
    """Everything in one pickup is due at the same instant, whenever it arrived."""
    await a_shelf(db_session)
    await an_instruction(db_session)
    service = PackagingService(db_session, clock, EventBus())
    late = await service.raise_parcel(
        CreatePackTask(order_id=await an_order(db_session), cutoff_at=NOW + timedelta(hours=6))
    )
    soon = await service.raise_parcel(
        CreatePackTask(order_id=await an_order(db_session), cutoff_at=NOW + timedelta(hours=1))
    )

    columns = {column.status: column for column in await board_columns(db_session, now=clock.now())}
    queued = [card.id for card in columns[PackStatus.CHECKED].tasks]

    assert queued == [soon.id, late.id]


async def test_a_parcel_with_no_van_booked_is_not_thereby_the_most_urgent(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    await a_shelf(db_session)
    await an_instruction(db_session)
    service = PackagingService(db_session, clock, EventBus())
    unbooked = await service.raise_parcel(CreatePackTask(order_id=await an_order(db_session)))
    booked = await service.raise_parcel(
        CreatePackTask(order_id=await an_order(db_session), cutoff_at=NOW + timedelta(hours=4))
    )

    columns = {column.status: column for column in await board_columns(db_session, now=clock.now())}
    queued = [card.id for card in columns[PackStatus.CHECKED].tasks]

    assert queued == [booked.id, unbooked.id]


async def test_a_card_carries_the_box_the_geometry_implies(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Beside the packer's own choice, never instead of it."""
    await a_parcel(db_session, clock)

    columns = {column.status: column for column in await board_columns(db_session, now=clock.now())}
    card = columns[PackStatus.CHECKED].tasks[0]

    assert card.recommended_tara_name == "Коробка A"
    assert card.tara_name == ""
    assert card.volumetric_grams == Decimal("372.4")
