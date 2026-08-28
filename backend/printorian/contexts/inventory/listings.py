"""How large the materials listing has got, and whether it is large enough to page.

`InventoryService.table()` returns every active spec in one response, which
`DATABASE-REVIEW` §9 records as a deliberate gap alongside the printers table.
`core.pagination` carries the argument and the trigger; this module takes the
reading inventory is responsible for.

**The response grows on two axes, so both are counted.** A row is a spec, and each
spec carries its live lots nested inside it — so a catalogue that stays the same
size still produces a larger response as spools arrive, and paging the specs alone
would not bound it. Either half reaching the trigger is enough, because either half
is enough to make the response the thing the trigger exists to catch.

**Two things this deliberately does not count.** Inactive specs, because unlike a
retired printer they are filtered out of the listing unconditionally — there is no
`include_inactive` here, so they are not rows this endpoint can be asked for. And
spent lots: `_to_view` keeps only lots with material left, so a consumed spool
leaves the response on its own. That is worth knowing next to the trigger in #45,
which names lots accumulating "without being retired" — as the listing stands, the
lot half tracks material on hand rather than receiving history, and it is
`GET /materials/lots`, which does not exist yet, that would grow the other way.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.core.pagination import ListingSize, capped_count, capped_size

#: The name this reading is reported under in ``/health/ready``.
LISTING = "materials"


async def material_listing_size(db: AsyncSession) -> ListingSize:
    """How many objects ``GET /materials`` would serialise: specs, plus their lots.

    One statement rather than two round trips. Both counts are capped and neither
    reads a row of the other's table, so the database does the pair for what a
    single one would cost — which matters on a path a probe calls every few
    seconds.
    """
    specs = select(MaterialSpec.id).where(MaterialSpec.is_active.is_(True))
    lots = (
        select(MaterialLot.id)
        .join(MaterialSpec, MaterialSpec.id == MaterialLot.spec_id)
        .where(MaterialSpec.is_active.is_(True), MaterialLot.remaining_grams > Decimal(0))
    )
    spec_rows, lot_rows = (
        await db.execute(
            select(capped_count(specs).scalar_subquery(), capped_count(lots).scalar_subquery())
        )
    ).one()
    return capped_size(LISTING, int(spec_rows), int(lot_rows))


async def materials_listing_oversized(db: AsyncSession) -> bool:
    """The whole check, as ``/health/ready`` calls it."""
    return (await material_listing_size(db)).past_trigger


__all__ = ["LISTING", "material_listing_size", "materials_listing_oversized"]
