"""«Оборачиваемость» and «Залежалое» — two answers the movement ledger can give.

Issue #35's second slice. Both are derived from `material_movements`, which is
the point of that ledger: a location overwritten in place answers "where is it"
and destroys "how long was it there", and these two figures are exactly the
destroyed question.

**Turnover is measured over lots that left the shelf.** Days on shelf is the
gap between a lot's `stock.received` row and its first outbound row — mounted,
issued or written off. A lot still on the shelf has no turnover yet; it is
counted beside the figure (`still_on_shelf`) and never inside it, because a
shelf full of spools nobody has touched would otherwise read as a fast one
(CLAUDE.md §1: the denominator is what was observed).

**Dead stock is a money figure, and this module keeps the two halves apart.**
`dead_stock` returns grams and, per lot, a value only where receiving recorded a
price (`MaterialLot.purchase_price`, written by `procurement.receiving` and by
nothing else). A lot with no price is counted (`unpriced_lots`) and adds nothing
to the total — a total that quietly omitted it would read as smaller than the
truth and look authoritative. The router serves the money half behind
`VIEW_FINANCIALS`, on its own route; the reasoning is in `api/routers/store.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.contexts.inventory.movements import (
    MOVED_ISSUED,
    MOVED_MOUNTED,
    MOVED_RECEIVED,
    MOVED_WRITTEN_OFF,
    MaterialMovement,
)
from printorian.contexts.inventory.policies import LocationKind
from printorian.core.ids import EntityId

#: What takes a lot off the shelf. `stock.moved` is cell to cell and does not.
OUTBOUND: frozenset[str] = frozenset({MOVED_MOUNTED, MOVED_ISSUED, MOVED_WRITTEN_OFF})

_DAY_PLACES = Decimal("0.1")
_MONEY_PLACES = Decimal("0.01")
SECONDS_PER_DAY = Decimal(86400)


# ------------------------------------------------------------------ the shape


@dataclass(frozen=True, slots=True)
class LotHistory:
    """One lot, with the three instants the two measures are built from."""

    lot_id: EntityId
    label: str
    family: str
    location_kind: LocationKind
    initial_grams: Decimal
    remaining_grams: Decimal
    purchase_price: Decimal | None
    #: The `stock.received` row, or ``None`` for a lot that predates the ledger.
    received_at: datetime | None
    #: The first outbound row, or ``None`` while the lot is still on the shelf.
    left_at: datetime | None
    #: The most recent row of any reason, or ``None`` for a lot never moved.
    last_moved_at: datetime | None


class TurnoverRow(BaseModel):
    family: str
    #: Lots that left the shelf in the window — the denominator of the mean.
    turned: int = 0
    #: Mean days between receipt and first outbound, one place. ``None`` with none.
    mean_days_on_shelf: Decimal | None = None
    #: Lots received and still on the shelf: counted beside, never inside.
    still_on_shelf: int = 0


class TurnoverReport(BaseModel):
    since: datetime
    until: datetime
    rows: list[TurnoverRow] = Field(default_factory=list)


class DeadStockLot(BaseModel):
    lot_id: EntityId
    label: str
    family: str
    remaining_grams: Decimal
    #: Since the last movement of any kind; a lot never moved is idle since receipt.
    idle_days: Decimal
    #: `purchase_price × remaining / initial`, or ``None`` where no price was recorded.
    value: Decimal | None = None


class DeadStockReport(BaseModel):
    """Money — served only behind `VIEW_FINANCIALS`."""

    idle_days: int
    lots: list[DeadStockLot] = Field(default_factory=list)
    total_grams: Decimal = Decimal(0)
    #: Over the priced lots only; `unpriced_lots` says how many it leaves out.
    total_value: Decimal = Decimal(0)
    unpriced_lots: int = 0


# ------------------------------------------------------------------ the rules


def _days(start: datetime, end: datetime) -> Decimal:
    return (Decimal(int((end - start).total_seconds())) / SECONDS_PER_DAY).quantize(_DAY_PLACES)


def turnover(lots: Sequence[LotHistory], *, since: datetime, until: datetime) -> list[TurnoverRow]:
    """Per family: how long a lot sat between arriving and leaving. Pure."""
    turned: dict[str, list[Decimal]] = {}
    waiting: dict[str, int] = {}
    for lot in lots:
        if lot.received_at is None or lot.received_at < since or lot.received_at > until:
            continue
        if lot.left_at is None:
            waiting[lot.family] = waiting.get(lot.family, 0) + 1
            continue
        turned.setdefault(lot.family, []).append(_days(lot.received_at, lot.left_at))
    rows: list[TurnoverRow] = []
    for family in sorted(set(turned) | set(waiting)):
        days = turned.get(family, [])
        rows.append(
            TurnoverRow(
                family=family,
                turned=len(days),
                mean_days_on_shelf=(
                    (sum(days, Decimal(0)) / Decimal(len(days))).quantize(_DAY_PLACES)
                    if days
                    else None
                ),
                still_on_shelf=waiting.get(family, 0),
            )
        )
    return rows


def dead_stock(lots: Sequence[LotHistory], *, now: datetime, idle_days: int) -> DeadStockReport:
    """Lots on the shelf with mass left that nothing has touched for ``idle_days``. Pure."""
    report = DeadStockReport(idle_days=idle_days)
    for lot in lots:
        if lot.location_kind is not LocationKind.STOCK or lot.remaining_grams <= 0:
            continue
        since = lot.last_moved_at or lot.received_at
        if since is None:
            # Never moved and never received through the ledger: nothing says how
            # long it has been there, and "idle since the dawn of time" is a guess.
            continue
        idle = _days(since, now)
        if idle < Decimal(idle_days):
            continue
        value: Decimal | None = None
        if lot.purchase_price is not None and lot.initial_grams > 0:
            value = (lot.purchase_price * lot.remaining_grams / lot.initial_grams).quantize(
                _MONEY_PLACES
            )
            report.total_value += value
        else:
            report.unpriced_lots += 1
        report.total_grams += lot.remaining_grams
        report.lots.append(
            DeadStockLot(
                lot_id=lot.lot_id,
                label=lot.label,
                family=lot.family,
                remaining_grams=lot.remaining_grams,
                idle_days=idle,
                value=value,
            )
        )
    report.lots.sort(key=lambda lot: lot.idle_days, reverse=True)
    return report


# ------------------------------------------------------------------- the read


async def lot_histories(db: AsyncSession) -> list[LotHistory]:
    """Every lot with the three instants, in one query over the ledger."""
    received = (
        select(MaterialMovement.lot_id, func.min(MaterialMovement.at).label("at"))
        .where(MaterialMovement.reason == MOVED_RECEIVED)
        .group_by(MaterialMovement.lot_id)
        .subquery()
    )
    left = (
        select(MaterialMovement.lot_id, func.min(MaterialMovement.at).label("at"))
        .where(MaterialMovement.reason.in_(OUTBOUND))
        .group_by(MaterialMovement.lot_id)
        .subquery()
    )
    last = (
        select(MaterialMovement.lot_id, func.max(MaterialMovement.at).label("at"))
        .group_by(MaterialMovement.lot_id)
        .subquery()
    )
    rows = await db.execute(
        select(MaterialLot, MaterialSpec.family, received.c.at, left.c.at, last.c.at)
        .join(MaterialSpec, MaterialSpec.id == MaterialLot.spec_id)
        .outerjoin(received, received.c.lot_id == MaterialLot.id)
        .outerjoin(left, left.c.lot_id == MaterialLot.id)
        .outerjoin(last, last.c.lot_id == MaterialLot.id)
    )
    return [
        LotHistory(
            lot_id=lot.id,
            label=lot.label,
            family=family,
            location_kind=lot.location_kind,
            initial_grams=lot.initial_grams,
            remaining_grams=lot.remaining_grams,
            purchase_price=lot.purchase_price,
            received_at=received_at,
            left_at=left_at,
            last_moved_at=last_at,
        )
        for lot, family, received_at, left_at, last_at in rows.all()
    ]


__all__ = [
    "OUTBOUND",
    "DeadStockLot",
    "DeadStockReport",
    "LotHistory",
    "TurnoverReport",
    "TurnoverRow",
    "dead_stock",
    "lot_histories",
    "turnover",
]
