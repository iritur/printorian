"""The ledger, and the claim that nothing rewrites a row in it.

The thesis of issue #35 is one sentence: a lot has a cell address, and every move
of a lot appends a row nothing can overwrite. The second half is what these tests
are for, and it is the half a gate cannot see — `mount_lot` overwriting five
location columns in place passed every gate in the repository while destroying the
answer to "where was this spool before".

Each test below is written so that removing the thing it is about fails it and
nothing else. That was checked by mutation where it could be, and where it could
not the test says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    MOVED_MOUNTED,
    MOVED_MOVED,
    MOVED_UNMOUNTED,
    MOVED_WRITTEN_OFF,
    CreateStorageCell,
    CreateStorageZone,
    InventoryService,
    LocationKind,
    PlacementService,
)
from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.inventory.movements import MaterialMovement
from printorian.core.errors import DomainRuleViolationError
from printorian.core.ids import EntityId, new_id
from tests.factories import ensure_lot, ensure_printer

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
LATER = AT + timedelta(hours=1)


async def _warehouse(session: AsyncSession, *addresses: str) -> PlacementService:
    """One zone and the cells asked for, with no declared capacity.

    Capacity is left out on purpose: these tests are about the ledger, and a cell
    that refuses a spool for being full would make them fail for a reason they are
    not about.
    """
    placement = PlacementService(session)
    await placement.create_zone(CreateStorageZone(code="A", name="Зона A"))
    for address in addresses:
        await placement.create_cell(CreateStorageCell(zone_code="A", address=address))
    return placement


async def _movements(session: AsyncSession, lot_id: EntityId) -> list[MaterialMovement]:
    rows = await session.scalars(
        select(MaterialMovement)
        .where(MaterialMovement.lot_id == lot_id)
        .order_by(MaterialMovement.sequence)
    )
    return list(rows)


async def test_a_movement_row_is_never_rewritten(db_session: AsyncSession) -> None:
    """Move a lot twice, then read the *first* row back field for field.

    Asserting on the latest row cannot see an overwrite: a ledger that updated one
    row in place would answer the second move correctly and have lost the first,
    which is precisely the failure `MaterialLot`'s location columns already have.
    So the first row is captured before the second move and compared afterwards.
    """
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)
    placement = await _warehouse(db_session, "A1-1", "A1-2")

    await placement.place_lot(lot_id, address="A1-1", at=AT)
    first = (await _movements(db_session, lot_id))[0]
    before = (
        first.id,
        first.sequence,
        first.reason,
        first.grams,
        first.remaining_after,
        first.at,
        first.from_address,
        first.to_address,
    )

    await placement.place_lot(lot_id, address="A1-2", at=LATER)

    rows = await _movements(db_session, lot_id)
    assert [row.sequence for row in rows] == [1, 2]
    again = rows[0]
    assert (
        again.id,
        again.sequence,
        again.reason,
        again.grams,
        again.remaining_after,
        again.at,
        again.from_address,
        again.to_address,
    ) == before
    # And the second row says where it came from, which is the fact the first move
    # created and an overwrite would have destroyed.
    assert (rows[1].from_address, rows[1].to_address) == ("A1-1", "A1-2")
    assert rows[1].reason == MOVED_MOVED


async def test_mounting_a_lot_records_the_cell_it_left(db_session: AsyncSession) -> None:
    """`mount_lot` overwrites the location columns; the movement is the only record.

    Deleting the `record_movement` call from `InventoryService.mount_lot` fails
    exactly this test and its pair below. The assertion is on `from_address`
    specifically, because a movement row that merely exists proves nothing — the
    cell is readable only *before* the overwrite, and getting that ordering wrong
    is the whole trap.
    """
    lot_id, printer_id = new_id(), new_id()
    await ensure_lot(db_session, lot_id)
    await ensure_printer(db_session, printer_id, name="P-01")
    placement = await _warehouse(db_session, "A1-1")
    await placement.place_lot(lot_id, address="A1-1", at=AT)

    await InventoryService(db_session).mount_lot(
        lot_id, printer_id=printer_id, ams_unit=0, ams_slot=2, at=LATER
    )

    latest = (await _movements(db_session, lot_id))[-1]
    assert latest.reason == MOVED_MOUNTED
    assert latest.from_address == "A1-1"
    assert latest.to_kind is LocationKind.PRINTER

    # A spool in a machine is not in a cell, and the map must not draw it in one.
    lot = await db_session.get(MaterialLot, lot_id)
    assert lot is not None
    assert lot.cell_id is None


async def test_unmounting_a_lot_records_where_it_went(db_session: AsyncSession) -> None:
    """The pair. Without it a spool leaves a machine and the ledger says nothing."""
    lot_id, printer_id = new_id(), new_id()
    await ensure_lot(db_session, lot_id)
    await ensure_printer(db_session, printer_id, name="P-02")
    inventory = InventoryService(db_session)
    await inventory.mount_lot(lot_id, printer_id=printer_id, ams_unit=0, ams_slot=1, at=AT)

    await inventory.unmount_lot(lot_id, shelf="стеллаж 2", at=LATER)

    latest = (await _movements(db_session, lot_id))[-1]
    assert latest.reason == MOVED_UNMOUNTED
    assert latest.from_kind is LocationKind.PRINTER
    assert latest.to_kind is LocationKind.STOCK
    assert latest.to_address == "стеллаж 2"


async def test_writing_off_more_than_remains_is_refused_and_changes_nothing(
    db_session: AsyncSession,
) -> None:
    """The refusal, and then the row — because a 4xx proves nothing about the reel.

    The bound is checked before `remaining_grams` is touched, so this asserts both
    halves: the ADR-0012 code with its structured details, and the mass still on
    the spool afterwards. Checking only the error is the shape a wait-list fix was
    corrected for once already.
    """
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)
    placement = PlacementService(db_session)

    with pytest.raises(DomainRuleViolationError) as raised:
        await placement.write_off(lot_id, grams=Decimal(1200), at=AT)

    assert raised.value.code == "error.inventory.write_off_exceeds_remaining"
    assert raised.value.details["requested_grams"] == "1200"

    lot = await db_session.get(MaterialLot, lot_id)
    assert lot is not None
    assert lot.remaining_grams == Decimal("1000.00")
    assert await _movements(db_session, lot_id) == []


async def test_a_write_off_reduces_the_lot_and_records_the_grams_that_left(
    db_session: AsyncSession,
) -> None:
    """The first decrement of `remaining_grams` in the system.

    ``remaining_within_initial`` (models.py) still has to hold afterwards, which it
    does trivially here and would not if a write-off ever *added* mass — the reason
    the CHECK is asserted rather than assumed.
    """
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)

    view = await PlacementService(db_session).write_off(
        lot_id, grams=Decimal(400), at=AT, note="брак"
    )

    assert view.remaining_grams == Decimal("600.00")
    row = (await _movements(db_session, lot_id))[-1]
    assert row.reason == MOVED_WRITTEN_OFF
    assert row.grams == Decimal("400.00")
    # Stored beside the figure rather than recomputed, so the row still reads
    # correctly after the lot has moved on.
    assert row.remaining_after == Decimal("600.00")
    assert row.to_kind is LocationKind.CONSUMED

    lot = await db_session.get(MaterialLot, lot_id)
    assert lot is not None
    assert lot.remaining_grams <= lot.initial_grams


async def test_placing_a_lot_into_the_cell_it_is_already_in_is_refused(
    db_session: AsyncSession,
) -> None:
    """A ledger of non-movements is noise — `credit_actually_moved`, one context on."""
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)
    placement = await _warehouse(db_session, "A1-1")
    await placement.place_lot(lot_id, address="A1-1", at=AT)

    with pytest.raises(DomainRuleViolationError) as raised:
        await placement.place_lot(lot_id, address="A1-1", at=LATER)

    assert raised.value.code == "error.inventory.movement_not_a_move"
    assert len(await _movements(db_session, lot_id)) == 1


async def test_a_full_cell_refuses_and_an_undeclared_one_never_does(
    db_session: AsyncSession,
) -> None:
    """«Full» is only sayable about a cell somebody declared a capacity for.

    Both halves in one test on purpose: the refusal is only meaningful beside the
    case that must *not* refuse. Treating a null capacity as full would block the
    whole warehouse on the day it is first described (ADR-0007).
    """
    first, second = new_id(), new_id()
    await ensure_lot(db_session, first, code="PLA-A")
    await ensure_lot(db_session, second, code="PLA-B")

    placement = PlacementService(db_session)
    await placement.create_zone(CreateStorageZone(code="A"))
    await placement.create_cell(CreateStorageCell(zone_code="A", address="A1-1", capacity_lots=1))
    await placement.create_cell(CreateStorageCell(zone_code="A", address="A1-2"))

    await placement.place_lot(first, address="A1-1", at=AT)
    with pytest.raises(DomainRuleViolationError) as raised:
        await placement.place_lot(second, address="A1-1", at=AT)
    assert raised.value.code == "error.inventory.cell_full"
    assert raised.value.details["capacity_lots"] == 1

    await placement.place_lot(second, address="A1-2", at=AT)
    third = new_id()
    await ensure_lot(db_session, third, code="PLA-C")
    # Same cell, no declared capacity, second spool: accepted, because "nobody has
    # said how much fits" is not "it is full".
    placed = await placement.place_lot(third, address="A1-2", at=AT)
    assert placed.cell == "A1-2"
