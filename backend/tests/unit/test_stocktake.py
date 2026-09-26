"""The stocktake, against the real database.

Issue #35's last row. What makes the count honest:

* **only spools in a cell are lined up** — a mounted spool is not on a shelf;
* **an uncounted line is left alone**, in the book and in the report;
* **close corrects the book to the count against what it says now**, one ledger
  row per spool that differed, and a write-off between opening and close is not
  a shortage;
* **the money is costed only where a price was recorded**.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    MOVED_COUNTED,
    LocationKind,
    StocktakeService,
    StocktakeStatus,
    summary_of,
    value_of,
)
from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.inventory.movements import MaterialMovement
from printorian.contexts.inventory.placement import PlacementService
from printorian.contexts.inventory.schemas import CreateStorageCell, CreateStorageZone
from printorian.contexts.inventory.stocktake_models import Stocktake, StocktakeLine
from printorian.core.errors import ConflictError, DomainRuleViolationError
from printorian.core.ids import EntityId, new_id
from tests.factories import ensure_lot

T0 = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


async def _shelved(db: AsyncSession, address: str, *, remaining: str = "1000") -> EntityId:
    lot_id = new_id()
    await ensure_lot(db, lot_id)
    lot = await db.get(MaterialLot, lot_id)
    assert lot is not None
    lot.remaining_grams = Decimal(remaining)
    await PlacementService(db).place_lot(lot_id, address=address, at=T0 - timedelta(days=1))
    return lot_id


async def _shelves(db: AsyncSession) -> None:
    placement = PlacementService(db)
    await placement.create_zone(CreateStorageZone(code="A", name="Зона A"))
    for address in ("A1-1", "A1-2"):
        await placement.create_cell(CreateStorageCell(zone_code="A", address=address))


async def test_only_spools_in_a_cell_are_lined_up_and_the_book_is_snapshotted(
    db_session: AsyncSession,
) -> None:
    await _shelves(db_session)
    on_shelf = await _shelved(db_session, "A1-1", remaining="640")
    mounted = await _shelved(db_session, "A1-2")
    lot = await db_session.get(MaterialLot, mounted)
    assert lot is not None
    lot.location_kind = LocationKind.PRINTER
    unplaced = new_id()
    await ensure_lot(db_session, unplaced)  # in stock, but nowhere anybody could be sent to

    stocktake = await StocktakeService(db_session).open(zone_code=None, at=T0)

    # The sequence is not reset between tests, so the shape rather than the value.
    assert re.fullmatch(r"ST-\d{5}", stocktake.number)
    assert [(line.lot_id, line.cell_address, line.expected_grams) for line in stocktake.lines] == [
        (on_shelf, "A1-1", Decimal(640))
    ]
    # Nothing has been counted, and that is a null on every line — not a zero.
    assert all(line.counted_grams is None for line in stocktake.lines)

    with pytest.raises(ConflictError) as raised:
        await StocktakeService(db_session).open(zone_code=None, at=T0)
    assert raised.value.code == "error.inventory.stocktake_open"


async def test_close_corrects_the_book_to_the_count_against_what_it_says_now(
    db_session: AsyncSession,
) -> None:
    await _shelves(db_session)
    short = await _shelved(db_session, "A1-1", remaining="800")
    over = await _shelved(db_session, "A1-2", remaining="300")
    untouched = await _shelved(db_session, "A1-2", remaining="500")
    service = StocktakeService(db_session)

    stocktake = await service.open(zone_code="A", at=T0)
    await service.count(stocktake.id, short, grams=Decimal(600), at=T0 + timedelta(hours=1))
    await service.count(stocktake.id, over, grams=Decimal(350), at=T0 + timedelta(hours=1))
    # Somebody wrote 100 g off the short spool while the count was under way. The
    # count of 600 is then 100 g *below* the book of 700, not 200 below the 800
    # the stocktake opened with — applying the snapshot would take the write-off
    # twice.
    await PlacementService(db_session).write_off(
        short, grams=Decimal(100), at=T0 + timedelta(hours=2)
    )

    await service.close(stocktake.id, at=T0 + timedelta(hours=3))

    lots = {
        lot.id: lot
        for lot in await db_session.scalars(
            select(MaterialLot).where(MaterialLot.id.in_([short, over, untouched]))
        )
    }
    assert lots[short].remaining_grams == Decimal(600)
    assert lots[over].remaining_grams == Decimal(350)
    assert lots[untouched].remaining_grams == Decimal(500)  # never counted: left alone

    lines = {line.lot_id: line for line in stocktake.lines}
    assert lines[short].variance_grams == Decimal(-100)
    assert lines[over].variance_grams == Decimal(50)
    assert lines[untouched].variance_grams is None
    assert stocktake.status is StocktakeStatus.CLOSED

    rows = list(
        await db_session.scalars(
            select(MaterialMovement).where(MaterialMovement.reason == MOVED_COUNTED)
        )
    )
    by_lot = {row.lot_id: row for row in rows}
    assert set(by_lot) == {short, over}
    assert (by_lot[short].grams, by_lot[short].remaining_after) == (Decimal(100), Decimal(600))
    assert (by_lot[over].grams, by_lot[over].remaining_after) == (Decimal(50), Decimal(350))
    assert by_lot[over].note == stocktake.number

    summary = summary_of(stocktake)
    assert (summary.positions, summary.counted, summary.matched, summary.short, summary.over) == (
        3,
        2,
        0,
        1,
        1,
    )

    with pytest.raises(DomainRuleViolationError) as raised:
        await service.count(stocktake.id, over, grams=Decimal(1), at=T0)
    assert raised.value.code == "error.inventory.stocktake_closed"


async def test_a_count_above_what_the_spool_started_with_is_refused_before_writing(
    db_session: AsyncSession,
) -> None:
    await _shelves(db_session)
    lot_id = await _shelved(db_session, "A1-1")
    service = StocktakeService(db_session)
    stocktake = await service.open(zone_code=None, at=T0)

    with pytest.raises(DomainRuleViolationError) as raised:
        await service.count(stocktake.id, lot_id, grams=Decimal(1001), at=T0)
    assert raised.value.code == "error.inventory.count_exceeds_initial"
    [line] = stocktake.lines
    assert line.counted_grams is None


def test_value_costs_only_priced_lines_and_counts_the_rest() -> None:
    priced_short, unpriced_short, priced_over, matched = (new_id() for _ in range(4))
    stocktake = Stocktake(
        id=new_id(),
        number="ST-00007",
        status=StocktakeStatus.CLOSED,
        opened_at=T0,
        closed_at=T0 + timedelta(hours=2),
    )
    stocktake.lines = [
        StocktakeLine(
            lot_id=lot_id,
            label=str(lot_id)[:8],
            family="PLA",
            expected_grams=Decimal(1000),
            variance_grams=variance,
        )
        for lot_id, variance in (
            (priced_short, Decimal(-250)),  # 25% of a 2000 ₽ spool → 500 ₽
            (unpriced_short, Decimal(-100)),
            (priced_over, Decimal(40)),  # 4% of 1500 ₽ → 60 ₽
            (matched, Decimal(0)),
        )
    ]

    value = value_of(
        stocktake,
        {
            priced_short: (Decimal(2000), Decimal(1000)),
            unpriced_short: (None, Decimal(1000)),
            priced_over: (Decimal(1500), Decimal(1000)),
            matched: (Decimal(900), Decimal(1000)),
        },
    )

    assert value.short_value == Decimal("500.00")
    assert value.over_value == Decimal("60.00")
    assert value.unpriced_lines == 1
