"""Procurement — what the farm buys, from whom, and what actually turned up.

Public interface. Two rules shape everything in here.

**An order is a record of a commitment, a receipt is a record of an arrival, and
neither is a stock level.** That is why receiving writes three things — a
receipt, a material lot, and nothing else — and why «сколько пришло» is summed
from the receipts rather than counted down on the line. A quantity decremented in
place answers one question and destroys the two the purchasing screen is built
on: when it came, and what it cost that day.

**The screen is split by money, not blurred.** A manager with `MANAGE_INVENTORY`
sees every order, its composition, its stage path and its stock consequences with
no ruble anywhere in the response; prices need `VIEW_FINANCIALS` and arrive
through their own route. Blanking the money instead would spell "not permitted"
the same way this system already spells "not measured" (ADR-0007), and one
spelling for two facts is how a screen ends up lying quietly.
"""

from printorian.contexts.procurement.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    Supplier,
)
from printorian.contexts.procurement.policies import (
    OPEN_STATUSES,
    RECEIVABLE,
    TRANSITIONS,
    PurchasableKind,
    PurchaseStatus,
    assert_transition,
    can_transition,
    needs_reorder,
)
from printorian.contexts.procurement.reads import (
    StockedItem,
    material_items,
    order_rows,
    ordered_codes,
    reorder_rows,
    seed_lines,
    status_counts,
)
from printorian.contexts.procurement.receiving import ARRIVING
from printorian.contexts.procurement.schemas import (
    AdvanceOrder,
    AssignSupplier,
    ConsequenceKind,
    CreatePurchaseLine,
    CreatePurchaseOrder,
    CreateSupplier,
    PurchaseLineCost,
    PurchaseLineView,
    PurchaseOrderCost,
    PurchaseOrderRow,
    PurchaseOrderView,
    PurchaseReceiptView,
    PurchaseStageView,
    PurchaseStatusCount,
    PurchasingBoard,
    ReceiveDelivery,
    ReceiveLine,
    ReorderConsequence,
    ReorderRow,
    SupplierView,
)
from printorian.contexts.procurement.service import ProcurementService
from printorian.contexts.procurement.views import PIPE, costs_of, received, stages, view_of

__all__ = [
    "ARRIVING",
    "OPEN_STATUSES",
    "PIPE",
    "RECEIVABLE",
    "TRANSITIONS",
    "AdvanceOrder",
    "AssignSupplier",
    "ConsequenceKind",
    "CreatePurchaseLine",
    "CreatePurchaseOrder",
    "CreateSupplier",
    "ProcurementService",
    "PurchasableKind",
    "PurchaseLineCost",
    "PurchaseLineView",
    "PurchaseOrder",
    "PurchaseOrderCost",
    "PurchaseOrderLine",
    "PurchaseOrderRow",
    "PurchaseOrderView",
    "PurchaseReceipt",
    "PurchaseReceiptView",
    "PurchaseStageView",
    "PurchaseStatus",
    "PurchaseStatusCount",
    "PurchasingBoard",
    "ReceiveDelivery",
    "ReceiveLine",
    "ReorderConsequence",
    "ReorderRow",
    "StockedItem",
    "Supplier",
    "SupplierView",
    "assert_transition",
    "can_transition",
    "costs_of",
    "material_items",
    "needs_reorder",
    "order_rows",
    "ordered_codes",
    "received",
    "reorder_rows",
    "seed_lines",
    "stages",
    "status_counts",
    "view_of",
]
