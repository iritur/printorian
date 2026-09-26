"""What the «Инвентаризация» panel and the «Расхождения» tile read.

Read-only, and the folds are pure so a test can hand them lines. Two rules:

* **every denominator is the lines that exist** — «проверено 12 из 14» counts
  the non-null `counted_grams` over the lines the stocktake lined up, never over
  a planned count or the whole store;
* **the money is a separate answer**. `StocktakeValue` costs a shortage or a
  surplus only where receiving recorded a `purchase_price` on the spool, counts
  the lines it could not cost, and is served behind `VIEW_FINANCIALS` on its
  own route. The counts and grams beside it are for anybody who can see the
  store (CLAUDE.md §1, the money sentence).

«Следующая» from the kit is **not** here: no setting says how often the farm
counts, and a date computed from an interval nobody chose is a promise nobody
made.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.inventory.schemas import (
    StocktakeDetail,
    StocktakeLineView,
    StocktakeSummary,
    StocktakeValue,
)
from printorian.contexts.inventory.stocktake_models import Stocktake
from printorian.core.errors import NotFoundError
from printorian.core.ids import EntityId

#: How many past counts the panel's history carries.
HISTORY_LIMIT = 20

_MONEY_PLACES = Decimal("0.01")


def summary_of(stocktake: Stocktake) -> StocktakeSummary:
    """The panel's five figures, folded from the lines."""
    lines = stocktake.lines
    counted = [line for line in lines if line.counted_grams is not None]
    # Variance is written at close. While the count is open the comparison is
    # against the book as it was snapshotted — the only figure available and the
    # one the person counting is looking at.
    signed = [
        (
            line.variance_grams
            if line.variance_grams is not None
            else (line.counted_grams or Decimal(0)) - line.expected_grams
        )
        for line in counted
    ]
    return StocktakeSummary(
        id=stocktake.id,
        number=stocktake.number,
        status=stocktake.status,
        zone_code=stocktake.zone_code,
        opened_at=stocktake.opened_at,
        closed_at=stocktake.closed_at,
        positions=len(lines),
        counted=len(counted),
        matched=sum(1 for v in signed if v == 0),
        short=sum(1 for v in signed if v < 0),
        over=sum(1 for v in signed if v > 0),
    )


def detail_of(stocktake: Stocktake) -> StocktakeDetail:
    return StocktakeDetail(
        **summary_of(stocktake).model_dump(),
        note=stocktake.note,
        lines=[
            StocktakeLineView(
                lot_id=line.lot_id,
                label=line.label,
                family=line.family,
                cell_address=line.cell_address,
                expected_grams=line.expected_grams,
                counted_grams=line.counted_grams,
                counted_at=line.counted_at,
                variance_grams=line.variance_grams,
            )
            for line in stocktake.lines
        ],
    )


def value_of(
    stocktake: Stocktake, prices: dict[EntityId, tuple[Decimal | None, Decimal]]
) -> StocktakeValue:
    """Shortage and surplus in money, where a price exists to cost them with.

    ``prices`` maps a lot to ``(purchase_price, initial_grams)``. A line whose
    spool has no recorded price is counted in ``unpriced_lines`` and adds nothing
    — costed at nought it would shrink a shortage and look authoritative.
    """
    short = Decimal(0)
    over = Decimal(0)
    unpriced = 0
    for line in stocktake.lines:
        variance = line.variance_grams
        if variance is None or variance == 0:
            continue
        price, initial = prices.get(line.lot_id, (None, Decimal(0)))
        if price is None or initial <= 0:
            unpriced += 1
            continue
        value = (price * abs(variance) / initial).quantize(_MONEY_PLACES)
        if variance < 0:
            short += value
        else:
            over += value
    return StocktakeValue(
        id=stocktake.id,
        number=stocktake.number,
        short_value=short,
        over_value=over,
        unpriced_lines=unpriced,
    )


class StocktakeReads:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def history(self, *, limit: int = HISTORY_LIMIT) -> Sequence[StocktakeSummary]:
        """Newest first. The open one, if any, is first because it opened last."""
        rows = await self._db.scalars(
            select(Stocktake)
            .options(selectinload(Stocktake.lines))
            .order_by(Stocktake.opened_at.desc())
            .limit(min(limit, HISTORY_LIMIT))
        )
        return [summary_of(row) for row in rows]

    async def detail(self, stocktake_id: EntityId) -> StocktakeDetail:
        return detail_of(await self._one(stocktake_id))

    async def value(self, stocktake_id: EntityId) -> StocktakeValue:
        stocktake = await self._one(stocktake_id)
        lot_ids = [line.lot_id for line in stocktake.lines]
        prices: dict[EntityId, tuple[Decimal | None, Decimal]] = {}
        if lot_ids:
            rows = await self._db.execute(
                select(MaterialLot.id, MaterialLot.purchase_price, MaterialLot.initial_grams).where(
                    MaterialLot.id.in_(lot_ids)
                )
            )
            prices = {lot_id: (price, initial) for lot_id, price, initial in rows}
        return value_of(stocktake, prices)

    async def _one(self, stocktake_id: EntityId) -> Stocktake:
        stocktake = await self._db.scalar(
            select(Stocktake)
            .options(selectinload(Stocktake.lines))
            .where(Stocktake.id == stocktake_id)
        )
        if stocktake is None:
            # A 404, never an empty count: an all-null stocktake reads as "everything
            # matched", which is a claim about a count that never happened.
            raise NotFoundError(
                "error.inventory.stocktake_not_found", stocktake_id=str(stocktake_id)
            )
        return stocktake


__all__ = ["HISTORY_LIMIT", "StocktakeReads", "detail_of", "summary_of", "value_of"]
