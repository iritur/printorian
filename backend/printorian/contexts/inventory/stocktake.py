"""Opening a stocktake, counting a shelf, and closing the book against the count.

The write half; `stocktake_reads.py` is what the panel reads. Split by
responsibility rather than by the line counter: this module changes rows and
the other only answers.

**Closing is the second path in the system that changes `remaining_grams`**, and
the only one that can move it *up*. `placement.write_off` was the first and
only decrementing path until this; both go through `record_movement`, so the
ledger says which of the two it was (`stock.counted` carries the stocktake's
number in its note). The bound is the same: a count above `initial_grams` is
refused at count time, before anything is written, because the lot's own CHECK
would otherwise refuse it at close — after every other line had been applied.

**An uncounted line is left alone.** Only lines somebody actually counted are
compared and corrected; the rest keep their book value and are reported as not
checked. A stocktake that adjusted every shelf nobody reached to zero would be
the loudest possible version of inventing a number.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.inventory.models import MaterialLot, StorageCell, StorageZone
from printorian.contexts.inventory.movements import MOVED_COUNTED
from printorian.contexts.inventory.placement import record_movement
from printorian.contexts.inventory.policies import LocationKind, StocktakeStatus
from printorian.contexts.inventory.stocktake_models import (
    STOCKTAKE_NUMBER_SEQUENCE,
    Stocktake,
    StocktakeLine,
)
from printorian.core.errors import ConflictError, DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId

_NUMBER_PREFIX = "ST"


class StocktakeService:
    """The three writes, in the order a person makes them."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def open(
        self,
        *,
        zone_code: str | None,
        at: datetime,
        actor_id: EntityId | None = None,
        note: str | None = None,
    ) -> Stocktake:
        """Snapshot the book for every spool on a shelf, and start counting.

        One open stocktake at a time, farm-wide: two counts of the same cell at
        once would each be right about a different moment and disagree with each
        other, and the ledger rows at close could not say which the book followed.

        Only lots **in a cell** are lined up. A spool in a machine or in the dryer
        is not on a shelf to be counted, and a lot in stock with no address is one
        nobody could be told where to look for — it is honest to leave it out and
        it stays out of the denominator.
        """
        open_one = await self._db.scalar(
            select(Stocktake).where(Stocktake.status == StocktakeStatus.OPEN)
        )
        if open_one is not None:
            raise ConflictError("error.inventory.stocktake_open", number=open_one.number)

        query = (
            select(MaterialLot)
            .join(StorageCell, MaterialLot.cell_id == StorageCell.id)
            .options(selectinload(MaterialLot.cell), selectinload(MaterialLot.spec))
            .where(
                MaterialLot.location_kind == LocationKind.STOCK,
                MaterialLot.remaining_grams > 0,
            )
            .order_by(StorageCell.address, MaterialLot.created_at)
        )
        if zone_code is not None:
            zone = await self._db.scalar(select(StorageZone).where(StorageZone.code == zone_code))
            if zone is None:
                raise NotFoundError("error.inventory.zone_not_found", code=zone_code)
            query = query.where(StorageCell.zone_id == zone.id)
        lots = list(await self._db.scalars(query))

        stocktake = Stocktake(
            number=await self._next_number(),
            status=StocktakeStatus.OPEN,
            zone_code=zone_code,
            note=note,
            opened_at=at,
            opened_by=actor_id,
        )
        stocktake.lines = [
            StocktakeLine(
                lot_id=lot.id,
                label=lot.label,
                family=lot.spec.family,
                cell_address=lot.cell_address,
                expected_grams=lot.remaining_grams,
            )
            for lot in lots
        ]
        self._db.add(stocktake)
        await self._db.flush()
        return stocktake

    async def count(
        self,
        stocktake_id: EntityId,
        lot_id: EntityId,
        *,
        grams: Decimal,
        at: datetime,
        actor_id: EntityId | None = None,
    ) -> Stocktake:
        """Write what was found for one spool. Counting again overwrites — while open."""
        stocktake = await self._open_one(stocktake_id)
        line = next((row for row in stocktake.lines if row.lot_id == lot_id), None)
        if line is None:
            raise NotFoundError("error.inventory.stocktake_line_not_found", lot_id=str(lot_id))
        lot = await self._db.get(MaterialLot, lot_id)
        if lot is not None and grams > lot.initial_grams:
            # Refused here, before the row is touched, rather than by the lot's
            # `remaining_within_initial` CHECK at close — a refusal that happens
            # after the write has changed something is not a refusal.
            raise DomainRuleViolationError(
                "error.inventory.count_exceeds_initial",
                counted_grams=str(grams),
                initial_grams=str(lot.initial_grams),
            )
        line.counted_grams = grams
        line.counted_at = at
        line.counted_by = actor_id
        await self._db.flush()
        return stocktake

    async def close(
        self,
        stocktake_id: EntityId,
        *,
        at: datetime,
        actor_id: EntityId | None = None,
    ) -> Stocktake:
        """Correct the book to the count, one ledger row per spool that differed.

        The variance is against `remaining_grams` **now**, not against the
        snapshot taken at opening: a spool written off between the two is not a
        shortage, and applying ``counted − expected`` would take the write-off
        twice. What is stored on the line is exactly what the ledger row carries.
        """
        stocktake = await self._open_one(stocktake_id)
        for line in stocktake.lines:
            if line.counted_grams is None:
                continue
            lot = await self._db.scalar(
                select(MaterialLot)
                .options(selectinload(MaterialLot.cell))
                .where(MaterialLot.id == line.lot_id)
            )
            if lot is None:
                continue
            variance = line.counted_grams - lot.remaining_grams
            line.variance_grams = variance
            if variance == 0:
                continue
            lot.remaining_grams = line.counted_grams
            await record_movement(
                self._db,
                lot,
                reason=MOVED_COUNTED,
                at=at,
                grams=abs(variance),
                actor_id=actor_id,
                from_kind=lot.location_kind,
                from_address=lot.cell_address,
                to_kind=lot.location_kind,
                to_address=lot.cell_address,
                note=stocktake.number,
            )
            # `record_movement` does not flush and `next_sequence` reads the
            # ledger: two rows before one flush would claim the same rung.
            await self._db.flush()
        stocktake.status = StocktakeStatus.CLOSED
        stocktake.closed_at = at
        stocktake.closed_by = actor_id
        await self._db.flush()
        return stocktake

    # -- internals -------------------------------------------------------

    async def _open_one(self, stocktake_id: EntityId) -> Stocktake:
        stocktake = await self._db.scalar(
            select(Stocktake)
            .options(selectinload(Stocktake.lines))
            .where(Stocktake.id == stocktake_id)
        )
        if stocktake is None:
            raise NotFoundError(
                "error.inventory.stocktake_not_found", stocktake_id=str(stocktake_id)
            )
        if stocktake.status is not StocktakeStatus.OPEN:
            raise DomainRuleViolationError(
                "error.inventory.stocktake_closed", number=stocktake.number
            )
        return stocktake

    async def _next_number(self) -> str:
        value = await self._db.scalar(select(STOCKTAKE_NUMBER_SEQUENCE.next_value()))
        return f"{_NUMBER_PREFIX}-{int(value or 1):05d}"


__all__ = ["StocktakeService"]
