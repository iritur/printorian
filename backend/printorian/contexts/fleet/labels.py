"""Labels for printers something else has already observed.

`printorian_printers_offline{printer,brand}` carries a brand so an alert can be
routed and a dashboard grouped without a second lookup, and the brand lives in the
`printers` table. That makes this a **label lookup**, and the distinction is the
whole reason it is a separate function with a docstring this long.

**This is never the roster.** The ids come from `core.driver_health` — the printers
the *worker* said it was driving on its last pass — and this only decorates them.
Selecting the printers table instead, to "make sure every machine is reported",
would report on machines the worker never tried to reach: the denominator mistake
root CLAUDE.md §1 is about, and the one that makes a farm look healthier the worse
its collection gets. `core.driver_health`'s own docstring makes the same argument
for the same reason.

A missing row is therefore not an error and not a gap in the reading. The printer
was observed; it is the *label* that is unknown, and the caller says so with an
empty brand rather than dropping a real measurement over a decoration.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.models import Printer


async def brands_for(db: AsyncSession, printer_ids: Iterable[str]) -> dict[str, str]:
    """The brand of each of these printers, keyed by the id string given.

    Ids arrive as strings because they came back out of Redis, so an id that is
    not a UUID at all is simply not looked up — a stale or hand-written key in the
    driver-state store must not turn a scrape into a 500 that hides every other
    series with it.

    Keyed by the caller's own string rather than by `UUID` so the caller can look a
    label up with the value it already has, without re-normalising the id and
    getting a miss on a differently-cased hex digit.
    """
    parsed: dict[UUID, str] = {}
    for printer_id in printer_ids:
        try:
            parsed[UUID(printer_id)] = printer_id
        except ValueError:
            continue
    if not parsed:
        return {}

    rows = await db.execute(select(Printer.id, Printer.brand).where(Printer.id.in_(list(parsed))))
    return {parsed[row_id]: brand for row_id, brand in rows if row_id in parsed}


__all__ = ["brands_for"]
