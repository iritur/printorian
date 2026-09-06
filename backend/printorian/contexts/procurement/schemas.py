"""DTOs crossing the procurement boundary.

**Two families, split by money, and the split is load-bearing.** Everything named
`...View` carries quantities, codes, names, statuses and dates and *no rubles at
all*; everything named `...Cost...` carries prices and nothing else worth reading
without them. That is what lets the router put one behind `MANAGE_INVENTORY` and
the other behind `MANAGE_INVENTORY` + `VIEW_FINANCIALS` (CLAUDE.md §1) instead of
serving one response with the money blanked — a blanked field is a null, a null
already means "not measured", and one spelling for two facts is how a screen ends
up saying the farm bought something for nothing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.procurement.policies import PurchasableKind, PurchaseStatus
from printorian.core.ids import EntityId

# ------------------------------------------------------------------ suppliers


class SupplierView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    code: str
    name: str
    kinds: list[str] = Field(default_factory=list)
    is_active: bool = True


class CreateSupplier(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    kinds: list[PurchasableKind] = Field(default_factory=list)


# --------------------------------------------------------------- the reorder list


class ConsequenceKind(StrEnum):
    """Why a low item matters — in whichever terms the farm can actually measure.

    There is deliberately **no** "coverage in months" arm, and its absence is the
    single most important decision in this module. `design/purchasing.html` shows
    «1.2 месяца» and «Покрытие после поставки 1.9 месяца», and nothing in this
    system measures material consumption: `material_lots.remaining_grams` is
    written exactly once, at lot creation (`inventory/service.py`), and no worker
    or service ever decrements it. A months figure would therefore be derived from
    lot count or from the roster, which is the ADR-0007 collapse this codebase
    keeps being bitten by — and it would be silent and flattering, because a farm
    with no telemetry would look like a farm with plenty of runway.

    So the row says `NOT_MEASURED` and the console draws an em dash. When
    consumption is genuinely recorded, a `COVERAGE` arm is added here and the
    tests in `test_procurement_reorder.py` are the ones that will have to change.
    """

    #: Queued print jobs are already promised this material — a measured fact,
    #: read from `production.committed_material`. The honest form of the kit's
    #: «ORD-2152 ждёт».
    COMMITTED_WORK = "committed_work"
    #: Nothing this system records can say what running out will cost.
    NOT_MEASURED = "not_measured"


class ReorderConsequence(BaseModel):
    """What the «Последствие» column is allowed to say about one row."""

    kind: ConsequenceKind
    #: Grams of queued work waiting on this material. Present only under
    #: `COMMITTED_WORK`; null is "not measured", never zero.
    committed_grams: Decimal | None = None
    committed_jobs: int | None = None


class ReorderRow(BaseModel):
    """One line of «Требуют заказа сейчас». No money — it is a stock fact."""

    kind: PurchasableKind
    item_code: str
    item_name: str
    #: What is left. Measured, and therefore never null on a row that exists at
    #: all: an item whose stock is unknown cannot be compared to a threshold, and
    #: is simply not listed rather than listed as zero.
    remaining: Decimal
    unit: str
    threshold: Decimal
    consequence: ReorderConsequence


# ------------------------------------------------------------------ the board


class PurchaseStatusCount(BaseModel):
    """One of the filter chips above the orders table."""

    status: PurchaseStatus
    count: int


class PurchaseOrderRow(BaseModel):
    """One line of the orders table. Quantities and dates, no rubles."""

    id: EntityId
    number: str
    status: PurchaseStatus
    #: Null while the kit's «не выбран» draft has no supplier yet.
    supplier_code: str | None = None
    supplier_name: str | None = None
    line_count: int
    #: Summed units across the lines, so the row can say «32 единицы» without the
    #: client fetching every line of every order to add them up.
    total_quantity: Decimal
    expected_at: datetime | None = None
    created_at: datetime


class PurchasingBoard(BaseModel):
    """The whole screen, read against one instant.

    One response for the reason the packing board is one: the chips, the orders
    and the reorder list all describe the same moment, and a client fanning out
    would show an order in two chips at once.
    """

    at: datetime
    reorder: list[ReorderRow] = Field(default_factory=list)
    orders: list[PurchaseOrderRow] = Field(default_factory=list)
    counts: list[PurchaseStatusCount] = Field(default_factory=list)
    total: int = 0


# ----------------------------------------------------------------- one order


class PurchaseStageView(BaseModel):
    """One step of the «Путь заказа» pipe, with the time it was actually entered."""

    status: PurchaseStatus
    #: Null for a stage not reached — and also for one skipped, which is the case
    #: worth naming: an order that went from paid straight to receiving has no
    #: transit time, and putting the receiving time here would invent the day a
    #: courier collected it.
    at: datetime | None = None
    is_current: bool = False


class PurchaseReceiptView(BaseModel):
    """One arrival against one line. Quantities only — the price is in `/costs`."""

    id: EntityId
    line_id: EntityId
    quantity: Decimal
    lot_number: str | None = None
    material_lot_id: EntityId | None = None
    received_at: datetime
    received_by: EntityId | None = None


class PurchaseLineView(BaseModel):
    """One thing ordered, and how much of it has turned up."""

    id: EntityId
    kind: PurchasableKind
    item_code: str
    item_name: str
    quantity: Decimal
    unit: str
    #: Summed from the receipts, never stored: two spellings of "how much came"
    #: are two answers that can disagree, and the receipts are the ones a price
    #: history is rebuilt from.
    received_quantity: Decimal
    #: Whether this build can put an arrival of this class into stock at all
    #: (`policies.RECEIVABLE`). Served so the console can disable the field
    #: rather than letting a buyer type a lot number the API will refuse.
    is_receivable: bool


class PurchaseOrderView(BaseModel):
    """One purchase order, in full, with no money anywhere in it."""

    id: EntityId
    number: str
    status: PurchaseStatus
    supplier: SupplierView | None = None
    note: str | None = None
    expected_at: datetime | None = None
    created_at: datetime
    stages: list[PurchaseStageView] = Field(default_factory=list)
    lines: list[PurchaseLineView] = Field(default_factory=list)
    receipts: list[PurchaseReceiptView] = Field(default_factory=list)


# ---------------------------------------------------------------- money only


class PurchaseLineCost(BaseModel):
    """What one line was quoted at."""

    line_id: EntityId
    item_code: str
    quantity: Decimal
    #: Null when nobody has quoted it. A draft raised off a threshold breach has
    #: no price, and a zero would read as "free" to the total below.
    unit_price: Decimal | None = None
    total: Decimal | None = None


class PurchaseOrderCost(BaseModel):
    """«Стоимость заказа» — behind `VIEW_FINANCIALS`, and nothing else is."""

    order_id: EntityId
    number: str
    lines: list[PurchaseLineCost] = Field(default_factory=list)
    #: Null while any line is unpriced. A subtotal of the priced lines presented
    #: as "the order total" is exactly the flattering half-truth ADR-0007 bans —
    #: it is smaller than the real figure and looks authoritative.
    total: Decimal | None = None
    #: How many lines could not contribute, so the console can say why the total
    #: is an em dash instead of leaving a person to guess.
    unpriced_lines: int = 0
    #: «Заморозится в остатках» — what this order will tie up on the shelf once
    #: received. Only the lines this build can actually receive count towards it,
    #: and it is null if any of those is unpriced, for the reason above.
    frozen_in_stock: Decimal | None = None


# ------------------------------------------------------------------- commands


class CreatePurchaseLine(BaseModel):
    kind: PurchasableKind
    item_code: str = Field(min_length=1, max_length=120)
    item_name: str = ""
    quantity: Decimal = Field(gt=0)
    unit: str = "piece"
    unit_price: Decimal | None = None


class CreatePurchaseOrder(BaseModel):
    """Raise a draft.

    ``seed_from_reorder`` is «Собрать один заказ»: everything currently over its
    threshold, on one order, in one act. The lines it produces are the reorder
    rows themselves, so an order raised this way carries no prices at all until a
    buyer quotes them.
    """

    note: str | None = None
    expected_at: datetime | None = None
    seed_from_reorder: bool = False
    lines: list[CreatePurchaseLine] = Field(default_factory=list)


class AssignSupplier(BaseModel):
    supplier_id: EntityId


class AdvanceOrder(BaseModel):
    to: PurchaseStatus


class ReceiveLine(BaseModel):
    """What arrived against one line."""

    line_id: EntityId
    quantity: Decimal = Field(gt=0)
    #: What was paid per unit today. Null is honest — a delivery counted at the
    #: door before the invoice caught up — and is not the same as free.
    unit_price_paid: Decimal | None = None
    #: The supplier's batch number, copied onto the stock row this creates. The
    #: thread a recall is pulled by, and the reason `material_lots.lot_number`
    #: exists and was never written until now.
    lot_number: str | None = None
    #: Where it physically went.
    shelf: str | None = None


class ReceiveDelivery(BaseModel):
    """One arrival, covering one or more lines of one order."""

    lines: list[ReceiveLine] = Field(min_length=1)


__all__ = [
    "AdvanceOrder",
    "AssignSupplier",
    "ConsequenceKind",
    "CreatePurchaseLine",
    "CreatePurchaseOrder",
    "CreateSupplier",
    "PurchaseLineCost",
    "PurchaseLineView",
    "PurchaseOrderCost",
    "PurchaseOrderRow",
    "PurchaseOrderView",
    "PurchaseReceiptView",
    "PurchaseStageView",
    "PurchaseStatusCount",
    "PurchasingBoard",
    "ReceiveDelivery",
    "ReceiveLine",
    "ReorderConsequence",
    "ReorderRow",
    "SupplierView",
]
