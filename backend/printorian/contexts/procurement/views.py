"""Turning one stored purchase order into the two things a screen asks for.

Two functions and they are deliberately in the same file, because they are the
two halves of one decision: `view_of` is everything about an order that is not
money, `costs_of` is the money and nothing else. Keeping them adjacent is what
makes it obvious in review when a ruble drifts across — which is the failure the
route split in `api/routers/purchasing.py` exists to prevent, and a diff is where
it would be caught.

Pure: an ORM object in, a DTO out, no session. That is what lets the transition
tests state a stage path without a database behind them.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.procurement.models import PurchaseOrder, PurchaseOrderLine
from printorian.contexts.procurement.policies import RECEIVABLE, PurchaseStatus
from printorian.contexts.procurement.schemas import (
    PurchaseLineCost,
    PurchaseLineView,
    PurchaseOrderCost,
    PurchaseOrderView,
    PurchaseReceiptView,
    PurchaseStageView,
    SupplierView,
)

#: The six stages the kit's «Путь заказа» pipe draws, in order.
#:
#: `CANCELLED` is not one of them: a cancelled order did not reach a seventh step,
#: it stopped at whichever one it was on, and drawing a seventh box would tell a
#: reader the opposite. The console renders the cancellation from the status.
PIPE: tuple[PurchaseStatus, ...] = (
    PurchaseStatus.DRAFT,
    PurchaseStatus.APPROVED,
    PurchaseStatus.PAID,
    PurchaseStatus.IN_TRANSIT,
    PurchaseStatus.RECEIVING,
    PurchaseStatus.STORED,
)


def stages(order: PurchaseOrder) -> list[PurchaseStageView]:
    """The pipe, with the time each stage was actually entered.

    Times come from the per-stage columns, never from `updated_at` and never
    interpolated: an order that went from paid straight to receiving has no
    transit time, and the pipe shows that step with an em dash rather than
    borrowing the neighbouring timestamp. `DRAFT`'s time is `created_at`, which
    is the moment the draft was raised and is therefore the real thing.
    """
    entered = {
        PurchaseStatus.DRAFT: order.created_at,
        PurchaseStatus.APPROVED: order.approved_at,
        PurchaseStatus.PAID: order.paid_at,
        PurchaseStatus.IN_TRANSIT: order.shipped_at,
        PurchaseStatus.RECEIVING: order.receiving_at,
        PurchaseStatus.STORED: order.stored_at,
    }
    return [
        PurchaseStageView(status=stage, at=entered[stage], is_current=stage is order.status)
        for stage in PIPE
    ]


def view_of(order: PurchaseOrder) -> PurchaseOrderView:
    """One purchase order, in full, with no money anywhere in it.

    Requires `lines`, each line's `receipts`, and `supplier` to be loaded — the
    service does that eagerly, because touching a lazy relationship under asyncio
    raises `MissingGreenlet` rather than quietly issuing a query.
    """
    return PurchaseOrderView(
        id=order.id,
        number=order.number,
        status=order.status,
        supplier=SupplierView.model_validate(order.supplier) if order.supplier else None,
        note=order.note,
        expected_at=order.expected_at,
        created_at=order.created_at,
        stages=stages(order),
        lines=[_line(line) for line in order.lines],
        receipts=[
            PurchaseReceiptView(
                id=receipt.id,
                line_id=receipt.line_id,
                quantity=receipt.quantity,
                lot_number=receipt.lot_number,
                material_lot_id=receipt.material_lot_id,
                received_at=receipt.received_at,
                received_by=receipt.received_by,
            )
            for line in order.lines
            for receipt in line.receipts
        ],
    )


def _line(line: PurchaseOrderLine) -> PurchaseLineView:
    return PurchaseLineView(
        id=line.id,
        kind=line.kind,
        item_code=line.item_code,
        item_name=line.item_name,
        quantity=line.quantity,
        unit=line.unit,
        received_quantity=received(line),
        is_receivable=line.kind in RECEIVABLE,
    )


def received(line: PurchaseOrderLine) -> Decimal:
    """How much of one line has actually arrived, summed from its receipts.

    Summed rather than stored, for the reason `PurchaseReceipt` exists at all: a
    counter on the line would be a second answer to the same question, and the two
    can disagree the first time a receipt is corrected.
    """
    return sum((receipt.quantity for receipt in line.receipts), start=Decimal(0))


def costs_of(order: PurchaseOrder) -> PurchaseOrderCost:
    """«Стоимость заказа» — served only behind `VIEW_FINANCIALS`.

    The total is null while any line is unpriced, rather than being a subtotal of
    the priced ones. A partial sum labelled "order total" is smaller than the real
    figure and reads as authoritative, which is the flattering half-truth ADR-0007
    is about; `unpriced_lines` is what lets the console say why it is an em dash.
    """
    lines = [
        PurchaseLineCost(
            line_id=line.id,
            item_code=line.item_code,
            quantity=line.quantity,
            unit_price=line.unit_price,
            total=None if line.unit_price is None else line.unit_price * line.quantity,
        )
        for line in order.lines
    ]
    unpriced = [line for line in lines if line.total is None]
    receivable = [line for line in order.lines if line.kind in RECEIVABLE]
    return PurchaseOrderCost(
        order_id=order.id,
        number=order.number,
        lines=lines,
        total=None if unpriced else sum((line.total or Decimal(0) for line in lines), Decimal(0)),
        unpriced_lines=len(unpriced),
        # «Заморозится в остатках»: only what this build can actually put on a
        # shelf ties up money there. A printer on the same order is capital
        # expenditure, not stock, and adding it in would answer a different
        # question than the one the panel asks.
        frozen_in_stock=(
            None
            if any(line.unit_price is None for line in receivable)
            else sum(
                ((line.unit_price or Decimal(0)) * line.quantity for line in receivable),
                Decimal(0),
            )
        ),
    )


__all__ = ["PIPE", "costs_of", "received", "stages", "view_of"]
