"""Material status, and where a lot physically is.

The scenario asks the materials table for status tags with counts — `stock`,
`in printer`, `ordered`, `none` — and for a location that is either a shelf or a
printer's AMS port.

Status is **derived** from the lots, never stored on the spec. That is what makes
the counter chips above the table correct by construction: they are a rollup, so
they cannot drift out of step with the stock they describe. V1 stored a status
field and had to remember to update it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MaterialStatus(StrEnum):
    """The four states the scenario's status tags count."""

    STOCK = "stock"
    IN_PRINTER = "in_printer"
    ORDERED = "ordered"
    #: Known to the catalogue, used before, none held and none on order.
    NONE = "none"


class LocationKind(StrEnum):
    STOCK = "stock"
    PRINTER = "printer"
    DRYER = "dryer"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True, kw_only=True)
class Location:
    """Where a physical lot is right now.

    Either a storage place (``shelf``) or a printer slot (``printer`` + ``ams_unit``
    + ``ams_slot``). Rendered by the client from these parts, never as prose here.
    """

    kind: LocationKind
    shelf: str | None = None
    printer_id: str | None = None
    ams_unit: int | None = None
    ams_slot: int | None = None

    @property
    def is_mounted(self) -> bool:
        return self.kind is LocationKind.PRINTER


def derive_status(
    *, has_stock_lots: bool, has_mounted_lots: bool, has_open_orders: bool
) -> MaterialStatus:
    """Roll physical reality up into one status for the table.

    Mounted wins over shelf stock: "where can I print this right now" is the
    question an operator is actually asking when scanning that column.
    """
    if has_mounted_lots:
        return MaterialStatus.IN_PRINTER
    if has_stock_lots:
        return MaterialStatus.STOCK
    if has_open_orders:
        return MaterialStatus.ORDERED
    return MaterialStatus.NONE
