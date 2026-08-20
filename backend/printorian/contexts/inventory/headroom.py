"""How much of each material there is, and where it is standing.

The dashboard's filament panel splits a material's mass three ways — **loaded**
into a machine, **on the shelf**, and **committed** to work already in the queue.
Two of those three are inventory's to answer; the third belongs to production,
which owns the queue, and the delivery layer joins them.

The split is the whole point of the panel. A farm with 4 kg of PETG-CF and 3.6 kg
of it already promised to queued jobs is a farm that is about to stall, and a
single "in stock: 4 000 g" figure says nothing about that. The committed column is
the number nobody tracks and the one that causes the stall.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import Case

from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.contexts.inventory.policies import LocationKind

#: Most materials the panel will show. Ordered by total mass, so the cut drops the
#: ones with least at stake — and a farm carrying more than this many active
#: materials reads them in the materials table, not on a summary screen.
HEADROOM_LIMIT = 12


class MaterialStock(BaseModel):
    """One material's mass, split by where it physically is."""

    code: str
    name: str
    family: str
    color_name: str
    color_hex: str
    #: In an AMS slot or on a spool holder — reachable by a machine right now.
    loaded_grams: Decimal
    #: On a shelf. Reachable by a person.
    stock_grams: Decimal
    #: Where the loaded spools are, so the panel can name the machines.
    loaded_printer_ids: list[str] = Field(default_factory=list)

    @property
    def total_grams(self) -> Decimal:
        return self.loaded_grams + self.stock_grams


async def headroom(db: AsyncSession, *, limit: int = HEADROOM_LIMIT) -> list[MaterialStock]:
    """Every active material carrying mass, heaviest first.

    Materials with nothing left are omitted rather than shown at zero: an empty
    material is a purchasing fact, and it is already the materials table's `out`
    status chip. Putting a zero-height bar in a headroom panel spends a row on the
    one case where headroom is not the question.
    """
    totals = (
        select(
            MaterialLot.spec_id.label("spec_id"),
            func.sum(func.coalesce(_when_loaded(), 0)).label("loaded"),
            func.sum(func.coalesce(_when_stocked(), 0)).label("stock"),
        )
        .where(MaterialLot.remaining_grams > 0)
        .group_by(MaterialLot.spec_id)
        .subquery()
    )

    rows = await db.execute(
        select(MaterialSpec, totals.c.loaded, totals.c.stock)
        .join(totals, totals.c.spec_id == MaterialSpec.id)
        .where(MaterialSpec.is_active.is_(True))
        .order_by((totals.c.loaded + totals.c.stock).desc())
        .limit(limit)
    )

    found = [
        MaterialStock(
            code=spec.code,
            name=spec.name,
            family=spec.family,
            color_name=spec.color_name,
            color_hex=spec.color_hex,
            loaded_grams=Decimal(str(loaded or 0)),
            stock_grams=Decimal(str(stock or 0)),
        )
        for spec, loaded, stock in rows.all()
    ]
    await _attach_printers(db, found)
    return found


def _when_loaded() -> Case[Decimal]:
    """A lot's mass, counted only while it is sitting in a machine."""
    return case(
        (MaterialLot.location_kind == LocationKind.PRINTER, MaterialLot.remaining_grams),
        else_=0,
    )


def _when_stocked() -> Case[Decimal]:
    """A lot's mass, counted only while it is somewhere a machine cannot reach."""
    return case(
        (MaterialLot.location_kind != LocationKind.PRINTER, MaterialLot.remaining_grams),
        else_=0,
    )


async def _attach_printers(db: AsyncSession, stocks: list[MaterialStock]) -> None:
    """Name the machines holding each material.

    A second query rather than a join, because the first one aggregates and this
    one does not: folding them together would multiply every sum by the number of
    loaded spools, which is the classic fan-out bug in a report that then has to be
    divided back out by hand.
    """
    if not stocks:
        return
    by_code = {stock.code: stock for stock in stocks}
    rows = await db.execute(
        select(MaterialSpec.code, MaterialLot.printer_id)
        .join(MaterialLot, MaterialLot.spec_id == MaterialSpec.id)
        .where(
            MaterialSpec.code.in_(by_code),
            MaterialLot.printer_id.is_not(None),
            MaterialLot.location_kind == LocationKind.PRINTER,
            MaterialLot.remaining_grams > 0,
        )
    )
    for code, printer_id in rows.all():
        target = by_code[code]
        if str(printer_id) not in target.loaded_printer_ids:
            target.loaded_printer_ids.append(str(printer_id))


__all__ = ["HEADROOM_LIMIT", "MaterialStock", "headroom"]
