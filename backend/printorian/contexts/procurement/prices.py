"""«Цены по ключевым позициям» — what a position has actually cost, over time.

The last row of issue #34: *price history per position falls out of receiving
and should not be a separate table of typed-in numbers.* It does fall out —
`purchase_receipts.unit_price_paid` is the price on the day a box arrived — and
this module is the read that turns those rows into the panel, plus nothing else.
No table, no typed-in number, no "current price" column anybody has to remember
to update.

Money, and therefore the second family of `schemas.py`: served only behind
`VIEW_FINANCIALS`, through its own route, refused whole rather than blanked.

Two rules keep the panel honest, and both are about the denominator:

* **A receipt with no recorded price is not a price of zero.** A delivery counted
  at the door before the invoice caught up is a real, common thing. Such a
  receipt is a delivery — it appears in `unpriced_receipts` so the screen can say
  «3 приёмки без цены» — and contributes to no figure. Averaging it in as `0`
  would drag every position towards free.
* **«Было» is an earlier priced receipt in the window, or nothing.** A position
  with one priced receipt has a price and no movement. Showing `0%` there would
  claim a stability nobody measured; showing the line's quoted `unit_price` as
  the earlier point would compare a quote with an invoice, which is a different
  question (ADR-0013's, in fact).

The kit also draws a «Средневзвешенно −9% за год» slab. It is not served: a
weighted average of relative changes across positions of different classes is a
composite whose weights nobody has chosen, and CLAUDE.md §1 is exactly about
serving a number like that as if the farm had measured it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.procurement.models import PurchaseOrderLine, PurchaseReceipt
from printorian.contexts.procurement.policies import PurchasableKind

# ------------------------------------------------------------------ the shape


class PositionPrice(BaseModel):
    """One line of the panel. Money — behind `VIEW_FINANCIALS`."""

    kind: PurchasableKind
    item_code: str
    item_name: str
    unit: str
    #: What the most recent *priced* receipt in the window paid per unit, and when.
    latest: Decimal
    latest_at: datetime
    #: The earliest priced receipt in the window, when it is not the same row as
    #: `latest`. Null is "one point, no movement" — not `0`, and not the quote.
    earliest: Decimal | None = None
    earliest_at: datetime | None = None
    #: `latest / earliest − 1`, to four places; null with `earliest`. Negative is
    #: cheaper, which the kit draws green.
    change: Decimal | None = None
    #: How many priced receipts the two figures were picked from.
    priced_receipts: int = 0
    #: Arrivals in the window whose price nobody recorded. Counted so the screen
    #: can say so; they are in none of the figures above.
    unpriced_receipts: int = 0


class PurchasePrices(BaseModel):
    """The panel, read against one window.

    `positions` is sorted by class then code so two reads of the same window put
    the same row in the same place — a leader list that reorders itself between
    refreshes is one a person cannot compare against yesterday.
    """

    since: datetime
    until: datetime
    positions: list[PositionPrice] = Field(default_factory=list)


@dataclass(frozen=True)
class ReceiptRow:
    """One receipt, flattened with its line, as the fold below reads it."""

    kind: PurchasableKind
    item_code: str
    item_name: str
    unit: str
    received_at: datetime
    unit_price_paid: Decimal | None


# ------------------------------------------------------------------- the rule


def fold_prices(rows: Sequence[ReceiptRow]) -> list[PositionPrice]:
    """Group receipts by position and pick each one's earliest and latest price.

    Pure, so the two denominator rules in the module docstring can be stated as
    one-line tests. Order of ``rows`` does not matter: the fold sorts by
    ``received_at`` itself rather than trusting the query to, because the day the
    query loses its ``ORDER BY`` is not the day anybody re-reads this.
    """
    by_position: dict[tuple[PurchasableKind, str], list[ReceiptRow]] = {}
    for row in rows:
        by_position.setdefault((row.kind, row.item_code), []).append(row)

    positions: list[PositionPrice] = []
    for (kind, item_code), group in sorted(
        by_position.items(), key=lambda kv: (kv[0][0], kv[0][1])
    ):
        priced = sorted(
            (row for row in group if row.unit_price_paid is not None),
            key=lambda row: row.received_at,
        )
        if not priced:
            # Deliveries, but no price on any of them: nothing to draw a line
            # through, and a row here would have to invent its own figure.
            continue
        last = priced[-1]
        first = priced[0] if len(priced) > 1 else None
        latest = last.unit_price_paid
        assert latest is not None  # narrowed by the filter above
        earliest = first.unit_price_paid if first is not None else None
        # The name and unit are taken from the latest receipt's line: a position
        # renamed in the catalogue reads as it is called now, not as it was.
        positions.append(
            PositionPrice(
                kind=kind,
                item_code=item_code,
                item_name=last.item_name or item_code,
                unit=last.unit,
                latest=latest,
                latest_at=last.received_at,
                earliest=earliest,
                earliest_at=first.received_at if first is not None else None,
                change=(
                    None
                    if earliest is None or earliest == 0
                    else (latest / earliest - 1).quantize(Decimal("0.0001"))
                ),
                priced_receipts=len(priced),
                unpriced_receipts=len(group) - len(priced),
            )
        )
    return positions


# ------------------------------------------------------------------- the read


async def price_movements(db: AsyncSession, *, since: datetime, until: datetime) -> PurchasePrices:
    """Every position with a priced receipt in ``[since, until]``.

    One query over receipts joined to their lines; the grouping is the pure fold
    above. Receipts on a cancelled order are *not* excluded: the box arrived and
    the money left, whatever the order was later marked as, and the price paid is
    a fact about the supplier rather than about the order's status.
    """
    rows = await db.execute(
        select(
            PurchaseOrderLine.kind,
            PurchaseOrderLine.item_code,
            PurchaseOrderLine.item_name,
            PurchaseOrderLine.unit,
            PurchaseReceipt.received_at,
            PurchaseReceipt.unit_price_paid,
        )
        .join(PurchaseOrderLine, PurchaseOrderLine.id == PurchaseReceipt.line_id)
        .where(PurchaseReceipt.received_at >= since, PurchaseReceipt.received_at <= until)
    )
    receipts = [
        ReceiptRow(
            kind=PurchasableKind(kind),
            item_code=item_code,
            item_name=item_name,
            unit=unit,
            received_at=received_at,
            unit_price_paid=unit_price_paid,
        )
        for kind, item_code, item_name, unit, received_at, unit_price_paid in rows.all()
    ]
    return PurchasePrices(since=since, until=until, positions=fold_prices(receipts))


__all__ = ["PositionPrice", "PurchasePrices", "ReceiptRow", "fold_prices", "price_movements"]
