"""Raising a purchase order and walking it along the six stages.

The arrival itself lives in `receiving.py`, which is a split by responsibility
rather than by line count: everything here changes only this context's own rows,
while receiving reaches across the boundary and writes stock. That is the seam
worth having a file at, and it is the same cut `intake.py` → `intake_routing.py`
took.

The inventory writer is *injected* rather than constructed here, through the
public `InventoryService` and nothing deeper — the arrangement `scheduling` uses
for `fleet`, enforced by `tools/check_context_isolation.py`. It is wired in
`api/deps.py`, and `test_procurement_wiring.py` exists because the last time a
collaborator was wired this way, deleting the wiring left every test green.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.inventory import InventoryService
from printorian.contexts.procurement.models import (
    PO_NUMBER_SEQUENCE,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from printorian.contexts.procurement.policies import PurchaseStatus, assert_transition
from printorian.contexts.procurement.receiving import receive
from printorian.contexts.procurement.schemas import (
    CreatePurchaseLine,
    CreatePurchaseOrder,
    CreateSupplier,
    PurchaseOrderCost,
    PurchaseOrderView,
    ReceiveDelivery,
    SupplierView,
)
from printorian.contexts.procurement.views import costs_of, view_of
from printorian.core.clock import Clock
from printorian.core.errors import ConflictError, NotFoundError, ValidationError
from printorian.core.ids import EntityId

#: Prefix for the number a buyer says out loud.
_NUMBER_PREFIX = "PO"


class ProcurementService:
    """Suppliers, purchase orders, and the stage path between them."""

    def __init__(self, session: AsyncSession, clock: Clock, *, lots: InventoryService) -> None:
        self._db = session
        self._clock = clock
        #: The only way this context writes stock. See `receiving.py`.
        self._lots = lots

    # ------------------------------------------------------------ suppliers

    async def add_supplier(self, data: CreateSupplier) -> SupplierView:
        existing = await self._db.scalar(select(Supplier).where(Supplier.code == data.code))
        if existing is not None:
            raise ConflictError("error.procurement.supplier_exists", supplier_code=data.code)
        supplier = Supplier(
            code=data.code,
            name=data.name,
            # Stored as the plain codes the enum carries, so the column keeps
            # meaning what it meant if a member is ever renamed in Python.
            kinds=[kind.value for kind in data.kinds],
        )
        self._db.add(supplier)
        await self._db.flush()
        return SupplierView.model_validate(supplier)

    async def suppliers(self) -> list[SupplierView]:
        rows = await self._db.scalars(select(Supplier).order_by(Supplier.name))
        return [SupplierView.model_validate(row) for row in rows]

    # ---------------------------------------------------------------- orders

    async def raise_order(
        self, data: CreatePurchaseOrder, *, seeded: Sequence[CreatePurchaseLine] = ()
    ) -> PurchaseOrderView:
        """Raise a draft, with the lines the caller asked for.

        ``seeded`` is «Собрать один заказ»: the reorder list, turned into lines by
        `reads.seed_lines` and handed in by the delivery layer, because the five
        purchasable classes are stocked by four different contexts and gathering
        them is composition rather than this context's business.
        """
        order = PurchaseOrder(
            number=await self._next_number(),
            status=PurchaseStatus.DRAFT,
            note=data.note,
            expected_at=data.expected_at,
        )
        order.lines = [_line(entry) for entry in (*seeded, *data.lines)]
        self._db.add(order)
        await self._db.flush()
        return await self.order(order.id)

    async def add_lines(
        self, order_id: EntityId, lines: Sequence[CreatePurchaseLine]
    ) -> PurchaseOrderView:
        """Add lines to an order that has not left the draft stage.

        Refused afterwards, and this is a rule rather than a convenience: once an
        order is approved, somebody has committed money against a specific list,
        and quietly extending it would make the approval a record of something
        that never happened.
        """
        order = await self._load(order_id)
        if order.status is not PurchaseStatus.DRAFT:
            raise ValidationError("error.procurement.order_not_draft", status=order.status.value)
        for entry in lines:
            order.lines.append(_line(entry))
        await self._db.flush()
        return await self.order(order_id)

    async def assign_supplier(self, order_id: EntityId, supplier_id: EntityId) -> PurchaseOrderView:
        """Say who this is being bought from.

        Its own action because the kit's draft shows «не выбран»: an order is
        raised off a threshold breach and the buyer decides where afterwards, so
        naming the supplier is a real decision somebody makes rather than a field
        filled in at creation.
        """
        order = await self._load(order_id)
        supplier = await self._db.get(Supplier, supplier_id)
        if supplier is None:
            raise NotFoundError("error.procurement.supplier_not_found")
        order.supplier_id = supplier.id
        await self._db.flush()
        return await self.order(order_id)

    async def advance(self, order_id: EntityId, to: PurchaseStatus) -> PurchaseOrderView:
        """Move one stage along the path, stamping the time it was entered.

        `assert_transition` refuses anything not in `TRANSITIONS` with both ends in
        the details (ADR-0012). The stamp is written here rather than by a database
        trigger so that the pipe's six times and the status can never describe two
        different orders of events.
        """
        order = await self._load(order_id)
        assert_transition(order.status, to)
        order.status = to
        _stamp(order, to, self._clock.now())
        await self._db.flush()
        return await self.order(order_id)

    async def cancel(self, order_id: EntityId) -> PurchaseOrderView:
        """Drop an order. Its lines and receipts stay — they are what happened."""
        return await self.advance(order_id, PurchaseStatus.CANCELLED)

    async def receive(
        self, order_id: EntityId, data: ReceiveDelivery, *, by: EntityId | None
    ) -> PurchaseOrderView:
        """Count a delivery in and put it into stock — see `receiving.py`.

        Delegated rather than written here so the one path that reaches across a
        context boundary and writes somebody else's rows is a file a reviewer can
        read whole. The injected `InventoryService` is handed on: this class never
        touches `MaterialLot` itself, which is what keeps the isolation check
        honest rather than merely satisfied.
        """
        order = await self._load(order_id)
        await receive(self._db, self._clock, self._lots, order=order, data=data, by=by)
        return await self.order(order_id)

    # ----------------------------------------------------------------- reads

    async def order(self, order_id: EntityId) -> PurchaseOrderView:
        return view_of(await self._load(order_id))

    async def costs(self, order_id: EntityId) -> PurchaseOrderCost:
        """The money for one order. Served only behind `VIEW_FINANCIALS`."""
        return costs_of(await self._load(order_id))

    # ------------------------------------------------------------- internals

    async def _load(self, order_id: EntityId) -> PurchaseOrder:
        """One order with everything a view needs, eagerly.

        Eager because touching a lazy relationship under asyncio raises
        `MissingGreenlet` rather than issuing a query — and `NotFoundError` rather
        than an empty order, because an all-null response reads as "this order
        bought nothing", which is a claim about a thing that does not exist
        (CLAUDE.md §1).
        """
        order = await self._db.scalar(
            select(PurchaseOrder)
            .options(
                selectinload(PurchaseOrder.supplier),
                selectinload(PurchaseOrder.lines).selectinload(PurchaseOrderLine.receipts),
            )
            .where(PurchaseOrder.id == order_id)
        )
        if order is None:
            raise NotFoundError("error.procurement.order_not_found")
        return order

    async def _next_number(self) -> str:
        """Sequential, human-quotable purchase-order numbers.

        From the sequence rather than `SELECT count(*)`: see
        :data:`PO_NUMBER_SEQUENCE` for why counting rows hands two concurrent
        buyers the same number. Tests run on real PostgreSQL (ADR-0021), so there
        is no dialect without sequences to fall back for.
        """
        value = await self._db.scalar(select(PO_NUMBER_SEQUENCE.next_value()))
        return f"{_NUMBER_PREFIX}-{int(value or 1):06d}"


def _line(entry: CreatePurchaseLine) -> PurchaseOrderLine:
    return PurchaseOrderLine(
        kind=entry.kind,
        item_code=entry.item_code,
        item_name=entry.item_name,
        quantity=entry.quantity,
        unit=entry.unit,
        unit_price=entry.unit_price,
    )


def _stamp(order: PurchaseOrder, stage: PurchaseStatus, at: datetime) -> None:
    """Record when a stage was entered, in that stage's own column.

    One column per stage rather than a single `status_changed_at`, because the
    pipe draws six times at once and one column can only remember the last move.
    A stage never reached keeps its null, which is what makes a skipped step
    render as an em dash instead of borrowing its neighbour's time.
    """
    column = {
        PurchaseStatus.APPROVED: "approved_at",
        PurchaseStatus.PAID: "paid_at",
        PurchaseStatus.IN_TRANSIT: "shipped_at",
        PurchaseStatus.RECEIVING: "receiving_at",
        PurchaseStatus.STORED: "stored_at",
        PurchaseStatus.CANCELLED: "cancelled_at",
    }.get(stage)
    # `DRAFT` has no column: the draft was entered when the row was created, and
    # `created_at` already says so.
    if column is not None:
        setattr(order, column, at)


__all__ = ["ProcurementService"]
