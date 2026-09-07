"""What the store screen reads: the cell map, one cell, and the movements feed.

Separate from `placement.py` because they are separate responsibilities rather
than because a counter tripped: that module decides and writes, this one only
answers. The split matters most for the one rule both sides could get wrong
independently — every denominator here is counted from rows that exist, never
from a configured target (CLAUDE.md §1), and there is one function
(`placement.fill_percent`) doing the division for both the cell and the zone.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.inventory.models import MaterialLot, StorageCell, StorageZone
from printorian.contexts.inventory.movements import MaterialMovement
from printorian.contexts.inventory.placement import cell_view, fill_percent
from printorian.contexts.inventory.schemas import (
    CellDetail,
    CellMap,
    LotView,
    MovementView,
    ZoneView,
)
from printorian.core.errors import NotFoundError
from printorian.core.ids import EntityId

#: How many movements one cell's panel carries. The feed proper is paged by
#: `since`; this is the "recent" the kit's cell popup shows, and a cell that has
#: been worked all week would otherwise return its whole history to draw six rows.
CELL_MOVEMENT_LIMIT = 20

#: Ceiling on the movements feed, so a `since` left at the epoch cannot ask for
#: every row the farm has ever written.
FEED_LIMIT = 200


class StoreViews:
    """Read-only answers about the warehouse."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def cell_map(self) -> CellMap:
        """Every zone and its cells, with fill counted from what exists."""
        zones = list(
            await self._db.scalars(
                select(StorageZone)
                .options(selectinload(StorageZone.cells))
                .order_by(StorageZone.code)
            )
        )
        occupancy = await self._occupancy_by_cell()

        views: list[ZoneView] = []
        cells_total = 0
        occupied_total = 0
        for zone in zones:
            cells = sorted(zone.cells, key=lambda cell: cell.address)
            occupied = sum(1 for cell in cells if occupancy.get(cell.id, 0) > 0)
            cells_total += len(cells)
            occupied_total += occupied
            views.append(
                ZoneView(
                    id=zone.id,
                    code=zone.code,
                    name=zone.name,
                    temp_c=zone.temp_c,
                    humidity_percent=zone.humidity_percent,
                    cell_count=len(cells),
                    occupied_cells=occupied,
                    # The denominator is the cells this zone actually has. Not a
                    # target, not a planned size — the observed roster, so a zone
                    # nobody has finished describing cannot read as healthy. A zone
                    # with no cells divides by nothing and answers `None`.
                    fill_percent=fill_percent(occupied, len(cells) or None),
                    cells=[
                        cell_view(
                            cell,
                            zone_code=zone.code,
                            lot_count=occupancy.get(cell.id, 0),
                        )
                        for cell in cells
                    ],
                )
            )
        return CellMap(zones=views, cells_total=cells_total, occupied_total=occupied_total)

    async def cell_detail(self, address: str) -> CellDetail:
        """One cell, its live lots oldest-first, and what has happened to it.

        An unknown address is a 404 rather than an empty map. An all-null response
        reads as "this cell holds nothing", which is a claim about a cell that does
        not exist — CLAUDE.md §1, third bullet.
        """
        cell = await self._db.scalar(
            select(StorageCell)
            .options(selectinload(StorageCell.zone))
            .where(StorageCell.address == address)
        )
        if cell is None:
            raise NotFoundError("error.inventory.cell_not_found", address=address)

        lots = list(
            await self._db.scalars(
                select(MaterialLot)
                .options(selectinload(MaterialLot.cell))
                .where(MaterialLot.cell_id == cell.id, MaterialLot.remaining_grams > 0)
                # FIFO, oldest first: the spool that has been sitting longest is
                # the one that should leave next, which is the whole reason the
                # kit's panel is ordered at all.
                .order_by(MaterialLot.created_at)
            )
        )
        movements = list(
            await self._db.scalars(
                select(MaterialMovement)
                .where(
                    or_(
                        MaterialMovement.from_address == address,
                        MaterialMovement.to_address == address,
                    )
                )
                .order_by(MaterialMovement.at.desc(), MaterialMovement.sequence.desc())
                .limit(CELL_MOVEMENT_LIMIT)
            )
        )
        return CellDetail(
            cell=cell_view(cell, zone_code=cell.zone.code, lot_count=len(lots)),
            lots=[LotView.model_validate(lot) for lot in lots],
            movements=[MovementView.model_validate(row) for row in movements],
        )

    async def movements(
        self,
        *,
        since: datetime | None = None,
        address: str | None = None,
        lot_id: EntityId | None = None,
        limit: int = FEED_LIMIT,
    ) -> list[MovementView]:
        """The movements feed, newest first.

        Filtering by ``address`` matches either side, because "what happened at
        A1-1" means both what arrived and what left — a feed that only matched
        arrivals would show a cell emptying as nothing at all.
        """
        query = select(MaterialMovement)
        if since is not None:
            query = query.where(MaterialMovement.at >= since)
        if address is not None:
            query = query.where(
                or_(
                    MaterialMovement.from_address == address,
                    MaterialMovement.to_address == address,
                )
            )
        if lot_id is not None:
            query = query.where(MaterialMovement.lot_id == lot_id)

        rows = await self._db.scalars(
            query.order_by(MaterialMovement.at.desc(), MaterialMovement.sequence.desc()).limit(
                min(limit, FEED_LIMIT)
            )
        )
        return [MovementView.model_validate(row) for row in rows]

    async def _occupancy_by_cell(self) -> dict[EntityId, int]:
        """Live lots per cell, in one query rather than one per cell.

        Only lots with mass left: an empty reel in a cell is waste awaiting
        disposal, and counting it would make a cell read as occupied by nothing.
        """
        rows = await self._db.execute(
            select(MaterialLot.cell_id, func.count())
            .where(MaterialLot.cell_id.is_not(None), MaterialLot.remaining_grams > 0)
            .group_by(MaterialLot.cell_id)
        )
        return {cell_id: int(count) for cell_id, count in rows if cell_id is not None}


__all__ = ["CELL_MOVEMENT_LIMIT", "FEED_LIMIT", "StoreViews"]
