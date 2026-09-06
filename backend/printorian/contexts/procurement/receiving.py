"""The arrival: counting a delivery in, and turning it into stock.

This is the near-irreversible path on the screen, so it gets the most care. A
receipt that created a spool nobody ordered, or counted a box twice, is not
undone by editing a row — the shelf now disagrees with the record, and the next
person to notice is a scheduler that thinks it has filament.

Three refusals, each a code with structured details (ADR-0012):

* `error.procurement.class_not_receivable` — the line is for a class this build
  has no stock model it may increment (`policies.RECEIVABLE`).
* `error.procurement.over_receipt` — more has now arrived than was ever ordered.
* `error.procurement.order_not_arriving` — the order is not at a stage where
  anything can turn up, so this is somebody on the wrong record.

**What this deliberately does not do.** `design/purchasing.html` says twice that
the purchase price reaches the tariff after receiving, and it is tempting to have
this function write `MaterialSpec.purchase_price_per_1000m`. It does not.
That column is a catalogue rate the pricing engine's numbers are derived from,
and a receipt moving it would be a rate edit with no settings-audit row and no
snapshot behind it — under ADR-0020, which guarantees that changing a rate never
reprices work already quoted, and which holds precisely because rate changes go
through the audit and the per-order snapshot. What arrives here is a
*measurement*: it is written to `purchase_receipts.unit_price_paid` and onto the
lot it created, and whether the catalogue price follows stays a person's
decision. If you are here to "finish" that link, it belongs in the settings path,
not in this file.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import CreateMaterialLot, InventoryService
from printorian.contexts.procurement.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
)
from printorian.contexts.procurement.policies import RECEIVABLE, PurchaseStatus
from printorian.contexts.procurement.schemas import ReceiveDelivery, ReceiveLine
from printorian.contexts.procurement.views import received
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import EntityId

#: Stages at which something can physically turn up.
#:
#: `PAID` is in the set on purpose: a courier who arrives before anybody moved
#: the order to «В пути» is the common case, and refusing the delivery because of
#: a status nobody updated would teach the floor to work around this screen.
ARRIVING: frozenset[PurchaseStatus] = frozenset(
    {PurchaseStatus.PAID, PurchaseStatus.IN_TRANSIT, PurchaseStatus.RECEIVING}
)


async def receive(
    db: AsyncSession,
    clock: Clock,
    lots: InventoryService,
    *,
    order: PurchaseOrder,
    data: ReceiveDelivery,
    by: EntityId | None,
) -> None:
    """Count a delivery against one order and put it into stock.

    One transaction — the caller's request-scoped session — so a lot write that
    fails takes its receipt with it. The order matters: the stock row is created
    *first*, and only then is the receipt written pointing at it. A receipt
    without its lot would say a spool is on the shelf that is not, which is the
    direction of error that costs a print; a lot without its receipt would merely
    be a spool whose paperwork is missing, and cannot happen anyway because the
    same failure rolls both back.
    """
    if order.status not in ARRIVING:
        raise ValidationError(
            "error.procurement.order_not_arriving",
            status=order.status.value,
            allowed=sorted(stage.value for stage in ARRIVING),
        )

    by_id = {line.id: line for line in order.lines}
    for entry in data.lines:
        line = by_id.get(entry.line_id)
        if line is None:
            # Not "ignore the line we do not recognise": a delivery half of which
            # silently did nothing is worse than one that was refused whole.
            raise NotFoundError("error.procurement.line_not_found", line_id=str(entry.line_id))
        _assert_receivable(line, entry)
        await _receive_one(db, clock, lots, line=line, entry=entry, by=by)

    # The order has now started arriving, whatever anybody had it marked as. Not
    # advanced to `STORED`: that is a person saying the shelf is straight, and
    # partial deliveries are why the two are different stages at all.
    if order.status is not PurchaseStatus.RECEIVING:
        order.status = PurchaseStatus.RECEIVING
        order.receiving_at = clock.now()
    await db.flush()


def _assert_receivable(line: PurchaseOrderLine, entry: ReceiveLine) -> None:
    """Refuse an arrival this build cannot honestly record.

    The over-receipt check counts what has *already* arrived, so two half
    deliveries that together exceed the order are caught on the second one — the
    case a per-request check against the ordered quantity alone would miss, and
    the one that actually happens.
    """
    if line.kind not in RECEIVABLE:
        raise ValidationError(
            "error.procurement.class_not_receivable",
            kind=line.kind.value,
            receivable=sorted(kind.value for kind in RECEIVABLE),
        )
    already = received(line)
    if already + entry.quantity > line.quantity:
        raise ValidationError(
            "error.procurement.over_receipt",
            line_id=str(line.id),
            ordered=str(line.quantity),
            already_received=str(already),
            arriving=str(entry.quantity),
        )


async def _receive_one(
    db: AsyncSession,
    clock: Clock,
    lots: InventoryService,
    *,
    line: PurchaseOrderLine,
    entry: ReceiveLine,
    by: EntityId | None,
) -> None:
    """One line's arrival: a stock row, then the receipt that explains it."""
    lot = await lots.add_lot(
        CreateMaterialLot(
            spec_code=line.item_code,
            initial_grams=entry.quantity,
            shelf=entry.shelf,
            lot_number=entry.lot_number,
            purchase_price=_lot_price(entry),
        )
    )
    receipt = PurchaseReceipt(
        line_id=line.id,
        quantity=entry.quantity,
        unit_price_paid=entry.unit_price_paid,
        lot_number=entry.lot_number,
        material_lot_id=lot.id,
        received_at=clock.now(),
        received_by=by,
    )
    db.add(receipt)
    # Appended so the in-memory line agrees with the database about what has
    # arrived: the next entry in this same delivery runs its over-receipt check
    # against `received(line)`, and a line whose receipts were only in the session
    # would let two entries in one request each pass on their own.
    line.receipts.append(receipt)
    await db.flush()


def _lot_price(entry: ReceiveLine) -> Decimal | None:
    """What the whole lot cost, from the price per unit that was paid.

    `material_lots.purchase_price` is a lot total, not a rate — it sits beside
    `initial_grams` on one physical spool. Null stays null: a delivery counted at
    the door before the invoice arrived has no price, and a zero would drag every
    later average down with a purchase that was never free.
    """
    if entry.unit_price_paid is None:
        return None
    return entry.unit_price_paid * entry.quantity


__all__ = ["ARRIVING", "receive"]
