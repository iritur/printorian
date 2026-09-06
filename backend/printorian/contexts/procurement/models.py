"""Persistent models for what the farm buys.

Four tables, and the shape is the one `ordering` already uses for the same
reason: an order is a header, its lines are what was asked for, and a receipt is
what actually turned up. The third table is the one people are tempted to skip —
"just decrement the line" — and skipping it loses the two facts the purchasing
screen is built on: *when* something arrived and *what it cost that day*. A
quantity counted down cannot answer either, and «Цены по ключевым позициям» is a
year of exactly those rows.

**Prices live on the line and the receipt, never on this header.** A line carries
what was quoted and a receipt what was paid, so an order total is a sum rather
than a stored figure that can drift from its parts. It also means the money is
confined to two tables, which is what lets the read side hand a manager a whole
purchase order with no rubles in it at all (`schemas`, and the route split in
`api/routers/purchasing.py`).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.procurement.policies import PurchasableKind, PurchaseStatus
from printorian.core.db import Base, Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId

#: Where human-quotable purchase-order numbers come from.
#:
#: The same decision `ordering.ORDER_NUMBER_SEQUENCE` records, and it is repeated
#: here rather than shared because the two counters must not interleave: a farm
#: reading «PO-000412» wants the four-hundred-and-twelfth purchase order, not the
#: four-hundred-and-twelfth row of any kind. ``SELECT count(*)`` would give two
#: concurrent buyers the same number and turn the unique constraint into a 500;
#: a sequence never repeats and nobody waits. Gaps appear when a draft is rolled
#: back, which is the accepted trade — a gap is cosmetic, a collision is not.
PO_NUMBER_SEQUENCE = Sequence("po_number_seq", start=1, metadata=Base.metadata)


class Supplier(Entity):
    """Somebody the farm buys from."""

    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("code", name="uq_suppliers_code"),)

    #: What a buyer types and says out loud, e.g. `FILAMENT-RU`.
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Which `PurchasableKind` values this supplier sells, as a list of codes.
    #:
    #: JSONB rather than a join table because nothing queries *across* suppliers
    #: by class — the screen asks "what does this one sell" while looking at one
    #: row — and JSONB rather than plain JSON because `json` reparses on every
    #: access and cannot be indexed (ADR-0017), which `test_schema_contracts`
    #: enforces for every column in this schema.
    kinds: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)
    #: A retired supplier keeps its orders. Deactivating is the ordinary act;
    #: deleting is refused by `purchase_orders.supplier_id`'s RESTRICT.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PurchaseOrder(Entity):
    """One order to one supplier, from «Черновик» to «На складе»."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("number", name="uq_purchase_orders_number"),
        Index("ix_purchase_orders_status_created_at", "status", "created_at"),
        Index("ix_purchase_orders_supplier_id", "supplier_id"),
    )

    #: e.g. `PO-000412`.
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Nullable because the kit's own draft shows «не выбран»: a buyer raises an
    #: order off a threshold breach and decides where to buy afterwards. A
    #: placeholder supplier would be an invented fact about who is being paid.
    #:
    #: ``RESTRICT``: deleting a supplier must not erase what was bought from it —
    #: the same argument `order_lines.model_asset_id` carries, and the reason the
    #: `is_active` flag above exists.
    supplier_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[PurchaseStatus] = mapped_column(
        enum_column(PurchaseStatus), nullable=False, default=PurchaseStatus.DRAFT
    )
    #: What a buyer typed about this order. Farm-authored prose, which ADR-0012
    #: does not govern — it governs what the backend says about itself.
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    #: When the supplier says it will arrive. A promise somebody made, so it is
    #: null until somebody makes one rather than defaulting to a lead time the
    #: farm invented on their behalf.
    expected_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # -- one timestamp per stage entered.
    #
    # A stage column each, rather than a single `status_changed_at`, because the
    # «Путь заказа» pipe draws six times at once and one column can only ever
    # remember the last move. They stay null until the stage is actually entered:
    # an order that went straight from PAID to RECEIVING has no transit time, and
    # writing one would be inventing the day a courier picked it up.
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    receiving_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    stored_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    supplier: Mapped[Supplier | None] = relationship()
    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class PurchaseOrderLine(Entity):
    """One thing ordered, in whatever class it belongs to."""

    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        Index("ix_purchase_order_lines_order_id", "order_id"),
        Index("ix_purchase_order_lines_kind_item_code", "kind", "item_code"),
        CheckConstraint("quantity > 0", name="purchase_line_quantity_positive"),
        CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0", name="purchase_line_price_non_negative"
        ),
    )

    #: ``CASCADE``: a line has no meaning without its order, and a purchase order
    #: deleted before it was ever approved should not leave orphan rows behind.
    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[PurchasableKind] = mapped_column(enum_column(PurchasableKind), nullable=False)
    #: The catalogue code *within that class* — a `material_specs.code`, a
    #: `packaging_tara.code`, a part number. Not a foreign key, because the five
    #: classes live in four contexts and two of them have no catalogue at all yet;
    #: a text code is what a buyer writes on the order either way.
    item_code: Mapped[str] = mapped_column(String(120), nullable=False)
    #: What the buyer wrote, in case the code is one the farm does not stock yet.
    item_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: A unit *code* — `gram`, `piece`, `roll` — rendered by the client, the same
    #: convention `packaging_tara.unit` uses. One bare number with no unit beside
    #: it is how a stock figure stops meaning anything.
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="piece")
    #: Null until somebody has a quote. A draft raised automatically off a
    #: threshold has no price, and a zero there would read as "free" to every
    #: total that sums this column (ADR-0007).
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    receipts: Mapped[list[PurchaseReceipt]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )


class PurchaseReceipt(Entity):
    """What actually arrived against one line, and what it cost that day.

    Insert-only, and the reason it is a table rather than a counter: a partial
    delivery followed by the rest is two arrivals at two prices, and the price
    history the purchasing screen is built on is the list of these rows. A
    `received_quantity` column on the line would answer "how much came" and
    nothing else.
    """

    __tablename__ = "purchase_receipts"
    __table_args__ = (
        Index("ix_purchase_receipts_line_id", "line_id"),
        Index("ix_purchase_receipts_material_lot_id", "material_lot_id"),
        Index("ix_purchase_receipts_received_by", "received_by"),
        CheckConstraint("quantity > 0", name="purchase_receipt_quantity_positive"),
        CheckConstraint(
            "unit_price_paid IS NULL OR unit_price_paid >= 0",
            name="purchase_receipt_price_non_negative",
        ),
    )

    line_id: Mapped[EntityId] = mapped_column(
        ForeignKey("purchase_order_lines.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: What was paid per unit *on the day it arrived*, which is not necessarily
    #: what the line quoted. Null when nobody recorded a price — a delivery
    #: counted at the door before the invoice caught up is a real thing, and a
    #: zero here would drag the price history down with a purchase that was never
    #: free.
    unit_price_paid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: The supplier's batch number, copied onto the material lot it becomes. The
    #: thread a recall is pulled by.
    lot_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: The stock row this arrival created, when the class has one.
    #:
    #: ``SET NULL``: a spool written off or merged away must not delete the record
    #: that it was bought. Null also covers the four classes this build cannot
    #: receive into — see `policies.RECEIVABLE`.
    material_lot_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("material_lots.id", ondelete="SET NULL"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    #: ``SET NULL``: a person leaving the farm must not erase the deliveries they
    #: signed for, exactly as `packaging_tasks.operator_id` decides.
    received_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    line: Mapped[PurchaseOrderLine] = relationship(back_populates="receipts")


__all__ = [
    "PO_NUMBER_SEQUENCE",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PurchaseReceipt",
    "Supplier",
]
