"""How large the printers listing has got, and whether it is large enough to page.

`FleetService.table()` returns every printer in one response, which
`DATABASE-REVIEW` §9 records as a deliberate gap: a farm has tens of machines, so
an unbounded query there returns a bounded result. `core.pagination` carries the
argument in full and the trigger that ends it; this module takes the reading the
fleet is responsible for.

**Every printer, not the active ones.** `GET /printers` filters to active by
default and `include_inactive=true` lifts the filter, so the largest response the
endpoint can be *asked* for is the whole table — and that is the half worth
measuring, because retiring a machine sets `is_active` false and nothing ever
deletes the row. Counting only active printers would watch the number bounded by
the size of the farm and miss the one that only ever climbs.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.models import Printer
from printorian.core.pagination import ListingSize, capped_count, capped_size

#: The name this reading is reported under in ``/health/ready``.
LISTING = "printers"


async def printer_listing_size(db: AsyncSession) -> ListingSize:
    """How many rows ``GET /printers?include_inactive=true`` would return."""
    rows = (await db.execute(capped_count(select(Printer.id)))).scalar_one()
    return capped_size(LISTING, int(rows))


async def printers_listing_oversized(db: AsyncSession) -> bool:
    """The whole check, as ``/health/ready`` calls it."""
    return (await printer_listing_size(db)).past_trigger


__all__ = ["LISTING", "printer_listing_size", "printers_listing_oversized"]
