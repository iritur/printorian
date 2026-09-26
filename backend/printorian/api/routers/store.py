"""The warehouse: the cell map, one cell, the movement ledger, and its two measures.

**One route here carries money, and it is the only one.** `design/store.html`
draws «Стоимость остатков» and «Залежалое» beside the fill figure. The second is
`/dead-stock`, in the shape `api/routers/jobs.py` uses for its financial route: a
separate endpoint with `VIEW_FINANCIALS` on top of the production gate, never a
field appended to a response an operator already reads. The first is still not
drawn — `MaterialLot.purchase_price` is written by receiving alone, so on a farm
that has not received through it the tile would read `0 ₽` over several hundred
thousand roubles of filament, an invented number in a nicer font (ADR-0007).
That is the split `VIEW_FINANCIALS` exists to make, and money has reached the
floor through a second door here before.

Drying is the other read-time figure on this router: a spool's state is one
stored instant (`dried_at`) against the clock and two settings, resolved per
request in `_drying_policy`, never stored on the row.

Reads need `VIEW_PRODUCTION` and writes need `MANAGE_INVENTORY`, both declared on
the router rather than per route — an operator finds the spool, a manager decides
where it goes and what leaves the reel.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from printorian.api.deps import AppClock, CurrentActor, DbSession, FarmSettings, requires
from printorian.contexts.identity import Permission
from printorian.contexts.inventory import (
    CellDetail,
    CellMap,
    CellView,
    CreateStorageCell,
    CreateStorageZone,
    DeadStockReport,
    DryingPolicy,
    DryingService,
    DryLot,
    LotView,
    MovementView,
    PlaceLot,
    PlacementService,
    StoreViews,
    TurnoverReport,
    WriteOffLot,
    ZoneView,
    dead_stock,
    lot_histories,
    turnover,
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
#: The second gate on the one route here that carries rubles. On top of the
#: router's `VIEW_PRODUCTION`, never instead of it, and never as a nulled field
#: on a response an operator already reads — the shape `api/routers/jobs.py`
#: set for `/jobs/variances`.
_MONEY = Depends(requires(Permission.VIEW_FINANCIALS))


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


async def _drying_policy(farm: FarmSettings) -> DryingPolicy:
    """The two drying settings, resolved — the first reader either has had.

    Composed here rather than as a `resolve_drying` on the settings context,
    because the two scalar resolvers already exist and a settings-side import
    of `inventory` would be a new edge in the layering for one dataclass.
    """
    return DryingPolicy(
        required=await farm.resolve_bool("inventory.require_drying"),
        valid_hours=await farm.resolve_int("inventory.drying_valid_hours"),
    )


@router.get("/cells/{address}")
async def cell_detail(
    address: str, db: DbSession, clock: AppClock, farm: FarmSettings
) -> CellDetail:
    """One cell: its live lots oldest-first, and what has happened to it.

    An unknown address is a 404. Answering an empty map instead would say "this
    cell holds nothing" about a cell that does not exist, and the two readings are
    indistinguishable to whoever is standing in the aisle (CLAUDE.md §1).

    Each lot carries its drying state, computed against the clock and the farm's
    `inventory.drying_valid_hours` at this moment — never stored, so a spool
    nobody looked at cannot go on reading as dry.
    """
    return await StoreViews(db).cell_detail(
        address, now=clock.now(), drying=await _drying_policy(farm)
    )


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

    The irreversible one, and — with a stocktake's close — one of the two paths
    in the system that change `remaining_grams`. The bound is checked before the
    row is touched, so a refusal leaves the reel exactly as it was.
    """
    return await PlacementService(db).write_off(
        lot_id,
        grams=data.grams,
        at=clock.now(),
        actor_id=actor.user_id,
        note=data.note,
    )


@router.post("/lots/{lot_id}/dry", dependencies=[_MANAGES])
async def send_to_dryer(
    lot_id: EntityId,
    data: DryLot,
    db: DbSession,
    actor: CurrentActor,
    clock: AppClock,
) -> LotView:
    """«Отправить на сушку» — into the dryer, keeping its cell for the way back.

    Only from stock: a spool in a machine is unmounted first, through the path
    that records which machine it left.
    """
    return await DryingService(db).send_to_dryer(
        lot_id, at=clock.now(), actor_id=actor.user_id, note=data.note
    )


@router.post("/lots/{lot_id}/dried", dependencies=[_MANAGES])
async def mark_dried(
    lot_id: EntityId,
    data: DryLot,
    db: DbSession,
    actor: CurrentActor,
    clock: AppClock,
) -> LotView:
    """Out of the dryer with a fresh mark. Refused for a spool that was never sent,
    which is what keeps `dried_at` meaning what its name says."""
    return await DryingService(db).mark_dried(
        lot_id, at=clock.now(), actor_id=actor.user_id, note=data.note
    )


@router.get("/turnover")
async def turnover_report(
    db: DbSession,
    clock: AppClock,
    days: Annotated[int, Query(ge=1, le=3660)] = 90,
) -> TurnoverReport:
    """«Оборачиваемость» — days between a lot arriving and leaving, per family.

    Over lots that *left* the shelf in the window; the ones still there are
    counted beside the mean and never inside it (`store_measures.turnover`).
    Grams and days only — the money half of the same ledger is `/dead-stock`.
    """
    until = clock.now()
    since = until - timedelta(days=days)
    return TurnoverReport(
        since=since, until=until, rows=turnover(await lot_histories(db), since=since, until=until)
    )


@router.get("/dead-stock", dependencies=[_MONEY])
async def dead_stock_report(
    db: DbSession,
    clock: AppClock,
    idle_days: Annotated[int, Query(ge=1, le=3660)] = 60,
) -> DeadStockReport:
    """«Залежалое» — what is on the shelf that nothing has touched, and what it cost.

    Behind `VIEW_FINANCIALS` because it carries a value per lot. The value is
    `purchase_price` pro rata to what is left, and only where receiving recorded a
    price; the unpriced lots are counted, not costed at nought.
    """
    return dead_stock(await lot_histories(db), now=clock.now(), idle_days=idle_days)
