"""The stocktake: open, count, close, and the two answers it leaves behind.

Its own router rather than five more routes on `store.py`, which is at the seam
already: this is a different question — not "where is the spool" but "is the
book right about it".

The permission shape is the store's. Reads take the router's `VIEW_PRODUCTION`;
the three writes take `MANAGE_INVENTORY`, because closing a count *changes the
book*, and that is the manager's call. `/value` is money — a shortage in
roubles — and sits behind `VIEW_FINANCIALS` on its own route, never as a field
on the summary the floor reads (the `/store/dead-stock` shape, for the same
reason).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, status

from printorian.api.deps import AppClock, CurrentActor, DbSession, requires
from printorian.contexts.identity import Permission
from printorian.contexts.inventory import (
    CountLine,
    OpenStocktake,
    StocktakeDetail,
    StocktakeReads,
    StocktakeService,
    StocktakeSummary,
    StocktakeValue,
    detail_of,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/store/stocktakes",
    tags=["store"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

_MANAGES = Depends(requires(Permission.MANAGE_INVENTORY))
_MONEY = Depends(requires(Permission.VIEW_FINANCIALS))


@router.get("")
async def history(db: DbSession) -> list[StocktakeSummary]:
    """Past counts, newest first; an open one is first because it opened last.

    Every figure in a summary is counted over the lines that stocktake lined up
    — «проверено 12 из 14» has the lines as its denominator, never the store.
    """
    return list(await StocktakeReads(db).history())


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGES])
async def open_stocktake(
    data: OpenStocktake, db: DbSession, actor: CurrentActor, clock: AppClock
) -> StocktakeDetail:
    """Snapshot the book for every spool in a cell and start counting.

    One at a time, farm-wide: a second open count is a 409 naming the first.
    """
    stocktake = await StocktakeService(db).open(
        zone_code=data.zone_code, at=clock.now(), actor_id=actor.user_id, note=data.note
    )
    return detail_of(stocktake)


@router.get("/{stocktake_id}")
async def stocktake_detail(stocktake_id: EntityId, db: DbSession) -> StocktakeDetail:
    """One count with its lines. An unknown id is a 404, never an empty count."""
    return await StocktakeReads(db).detail(stocktake_id)


@router.post("/{stocktake_id}/lines/{lot_id}", dependencies=[_MANAGES])
async def count_line(
    stocktake_id: EntityId,
    lot_id: EntityId,
    data: CountLine,
    db: DbSession,
    actor: CurrentActor,
    clock: AppClock,
) -> StocktakeDetail:
    """Write what was found on one spool. Counting again overwrites, while open.

    A count above the spool's `initial_grams` is refused here, before anything
    is written, rather than by the lot's own CHECK at close.
    """
    stocktake = await StocktakeService(db).count(
        stocktake_id,
        lot_id,
        grams=Decimal(data.counted_grams),
        at=clock.now(),
        actor_id=actor.user_id,
    )
    return detail_of(stocktake)


@router.post("/{stocktake_id}/close", dependencies=[_MANAGES])
async def close_stocktake(
    stocktake_id: EntityId, db: DbSession, actor: CurrentActor, clock: AppClock
) -> StocktakeDetail:
    """Correct the book to the count — the irreversible step.

    One `stock.counted` ledger row per spool that differed, against what the
    book says *now*. Lines nobody counted are left exactly as they were.
    """
    stocktake = await StocktakeService(db).close(
        stocktake_id, at=clock.now(), actor_id=actor.user_id
    )
    return detail_of(stocktake)


@router.get("/{stocktake_id}/value", dependencies=[_MONEY])
async def stocktake_value(stocktake_id: EntityId, db: DbSession) -> StocktakeValue:
    """«Недостача» and «Излишек» in roubles, where a price exists to cost them.

    Behind `VIEW_FINANCIALS` on top of the router's gate. Lines whose spool has
    no recorded price are counted in `unpriced_lines` and add nothing.
    """
    return await StocktakeReads(db).value(stocktake_id)
