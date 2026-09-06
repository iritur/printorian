"""The warehouse: the cell map, one cell, and the movement ledger.

**Nothing on this router carries money, and that is a decision rather than an
omission.** `design/store.html` draws «Стоимость остатков» and «Залежалое» beside
the fill figure, and both are unmeasurable today: `MaterialLot.purchase_price`
exists and is written by nothing, so a value tile would read `0 ₽` on a farm
holding several hundred thousand roubles of filament — an invented number in a
nicer font (ADR-0007). When dead stock arrives it takes the shape
`api/routers/jobs.py` uses for its financial route: a separate endpoint with
`VIEW_FINANCIALS` on top of the production gate, never a field appended to a
response an operator already reads. That is the split `VIEW_FINANCIALS` exists to
make, and money has reached the floor through a second door here before.

Reads need `VIEW_PRODUCTION` and writes need `MANAGE_INVENTORY`, both declared on
the router rather than per route — an operator finds the spool, a manager decides
where it goes and what leaves the reel.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status

from printorian.api.deps import AppClock, CurrentActor, DbSession, requires
from printorian.contexts.identity import Permission
from printorian.contexts.inventory import (
    CellDetail,
    CellMap,
    CellView,
    CreateStorageCell,
    CreateStorageZone,
    LotView,
    MovementView,
    PlaceLot,
    PlacementService,
    StoreViews,
    WriteOffLot,
    ZoneView,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/store",
    tags=["store"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

#: Writes sit behind the inventory permission. Declared once and attached per
#: route, because the router-level dependency above is the *read* gate and a
#: second router just for four POSTs would put the prefix in two places.
_MANAGES = Depends(requires(Permission.MANAGE_INVENTORY))


@router.get("/cells")
async def cell_map(db: DbSession) -> CellMap:
    """Every zone and its cells, with fill counted from the cells that exist.

    A cell whose `capacity_lots` nobody declared reports ``fill_percent: null``,
    and a zone with no cells does the same. The console draws no bar for either —
    an empty bar over four spools would be a claim the farm never made.
    """
    return await StoreViews(db).cell_map()


@router.get("/movements")
async def movements(
    db: DbSession,
    since: datetime | None = None,
    address: str | None = None,
    lot_id: EntityId | None = None,
) -> list[MovementView]:
    """The movements feed, newest first.

    Declared before `/cells/{address}` would be reached, but under its own path —
    `/movements` cannot be mistaken for an address, so the ordering here is for
    the reader rather than for the router.
    """
    return await StoreViews(db).movements(since=since, address=address, lot_id=lot_id)


@router.get("/cells/{address}")
async def cell_detail(address: str, db: DbSession) -> CellDetail:
    """One cell: its live lots oldest-first, and what has happened to it.

    An unknown address is a 404. Answering an empty map instead would say "this
    cell holds nothing" about a cell that does not exist, and the two readings are
    indistinguishable to whoever is standing in the aisle (CLAUDE.md §1).
    """
    return await StoreViews(db).cell_detail(address)


@router.post("/zones", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGES])
async def create_zone(data: CreateStorageZone, db: DbSession) -> ZoneView:
    return await PlacementService(db).create_zone(data)


@router.post("/cells", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGES])
async def create_cell(data: CreateStorageCell, db: DbSession) -> CellView:
    return await PlacementService(db).create_cell(data)


@router.post("/lots/{lot_id}/place", dependencies=[_MANAGES])
async def place_lot(
    lot_id: EntityId,
    data: PlaceLot,
    db: DbSession,
    actor: CurrentActor,
    clock: AppClock,
) -> LotView:
    """Put a lot into a cell, or move it between two, appending a movement."""
    return await PlacementService(db).place_lot(
        lot_id,
        address=data.address,
        at=clock.now(),
        actor_id=actor.user_id,
        note=data.note,
    )


@router.post("/lots/{lot_id}/write-off", dependencies=[_MANAGES])
async def write_off_lot(
    lot_id: EntityId,
    data: WriteOffLot,
    db: DbSession,
    actor: CurrentActor,
    clock: AppClock,
) -> LotView:
    """Take mass off a reel for good.

    The irreversible one, and the only path in the system that decrements
    `remaining_grams`. The bound is checked before the row is touched, so a
    refusal leaves the reel exactly as it was.
    """
    return await PlacementService(db).write_off(
        lot_id,
        grams=data.grams,
        at=clock.now(),
        actor_id=actor.user_id,
        note=data.note,
    )
