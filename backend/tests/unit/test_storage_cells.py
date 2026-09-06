"""The cell map, and the four numbers it must refuse to invent.

Every assertion here is CLAUDE.md §1 in a different costume: a fill percentage
over a capacity nobody declared, a zone fill over a target rather than over the
cells that exist, an empty zone reported as 0%, and a lot with no cell given a
placeholder address. Each has already been got wrong somewhere in this codebase,
which is why each gets its own named test rather than a shared one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    CreateStorageCell,
    CreateStorageZone,
    PlacementService,
    StoreViews,
)
from printorian.contexts.inventory.models import MaterialLot, StorageCell
from printorian.contexts.inventory.movements import MaterialMovement
from printorian.core.ids import new_id
from tests.factories import ensure_lot

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


async def test_a_cell_with_no_declared_capacity_reports_null_fill(
    db_session: AsyncSession,
) -> None:
    """Not 0, and not 100. Giving `capacity_lots` a default of 1 fails this alone.

    A cell holding one spool with a defaulted capacity of one reads as 100% full,
    which is what would stop an operator putting a second spool where there is
    room. Zero is the other direction and reads as empty over four spools. The
    honest answer is that nobody has said.
    """
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)
    placement = PlacementService(db_session)
    await placement.create_zone(CreateStorageZone(code="A"))
    await placement.create_cell(CreateStorageCell(zone_code="A", address="A1-1"))
    await placement.create_cell(CreateStorageCell(zone_code="A", address="A1-2", capacity_lots=4))
    await placement.place_lot(lot_id, address="A1-1", at=AT)

    zone = (await StoreViews(db_session).cell_map()).zones[0]
    undeclared = next(cell for cell in zone.cells if cell.address == "A1-1")
    declared = next(cell for cell in zone.cells if cell.address == "A1-2")

    assert undeclared.capacity_lots is None
    assert undeclared.fill_percent is None
    # The count itself is measured and is reported, because *that* the farm knows.
    assert undeclared.lot_count == 1
    assert declared.fill_percent == Decimal("0.0")


async def test_a_zone_fill_is_counted_from_the_cells_that_exist(
    db_session: AsyncSession,
) -> None:
    """The denominator rule, at the level where it is easiest to get wrong.

    Replacing the denominator with a configured target — a zone "planned for
    twenty cells" — makes a half-described zone read as under-used rather than as
    half described, and the error is silent and flattering (CLAUDE.md §1).
    """
    first, second = new_id(), new_id()
    await ensure_lot(db_session, first, code="PLA-A")
    await ensure_lot(db_session, second, code="PLA-B")

    placement = PlacementService(db_session)
    await placement.create_zone(CreateStorageZone(code="A"))
    for address in ("A1-1", "A1-2", "A1-3", "A1-4"):
        await placement.create_cell(CreateStorageCell(zone_code="A", address=address))
    await placement.place_lot(first, address="A1-1", at=AT)
    await placement.place_lot(second, address="A1-2", at=AT)

    zone = (await StoreViews(db_session).cell_map()).zones[0]
    assert zone.cell_count == 4
    assert zone.occupied_cells == 2
    assert zone.fill_percent == Decimal("50.0")


async def test_a_zone_with_no_cells_says_so_rather_than_reporting_zero_percent(
    db_session: AsyncSession,
) -> None:
    """Empty means nothing was declared there, not that nothing is stored there.

    The `core.driver_health` empty-roster precedent: dividing by a roster of zero
    and calling the answer 0% turns "we have not described this yet" into a claim
    about the farm's stock.
    """
    await PlacementService(db_session).create_zone(CreateStorageZone(code="Z", name="Новая"))

    zone = (await StoreViews(db_session).cell_map()).zones[0]
    assert zone.cell_count == 0
    assert zone.fill_percent is None


async def test_retiring_a_cell_leaves_the_movements_that_named_it_intact(
    db_session: AsyncSession,
) -> None:
    """The copied-address decision, proved rather than asserted in a comment.

    A foreign key from the ledger to `storage_cells` would leave two options and
    this test fails under both: ``SET NULL`` erases the address the row exists to
    carry, and ``RESTRICT`` makes the delete below impossible. Issued as SQL rather
    than through `session.delete`, because the ORM would do the work in Python and
    pass against a database holding no constraint at all.
    """
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)
    placement = PlacementService(db_session)
    await placement.create_zone(CreateStorageZone(code="A"))
    await placement.create_cell(CreateStorageCell(zone_code="A", address="A1-1"))
    await placement.place_lot(lot_id, address="A1-1", at=AT)

    await db_session.execute(delete(StorageCell).where(StorageCell.address == "A1-1"))
    await db_session.flush()
    # `SET NULL` happens in the database, behind the identity map's back.
    db_session.expire_all()

    row = await db_session.scalar(select(MaterialMovement).where(MaterialMovement.lot_id == lot_id))
    assert row is not None
    assert row.to_address == "A1-1"

    lot = await db_session.get(MaterialLot, lot_id)
    assert lot is not None
    assert lot.cell_id is None


async def test_a_lot_with_no_cell_reports_no_cell(db_session: AsyncSession) -> None:
    """Null, never an empty string and never a placeholder address (ADR-0007).

    An `''` here would render as a cell whose label happens to be blank, and the
    console cannot tell that apart from a cell nobody has named.
    """
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)

    lot = await db_session.get(MaterialLot, lot_id)
    assert lot is not None
    assert lot.cell_id is None
    assert lot.cell_address is None
