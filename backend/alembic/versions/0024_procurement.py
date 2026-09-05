"""what the farm buys, from whom, and what actually turned up

Four tables and one deletion, and the deletion is the point of the other four.

`material_specs.has_open_order` was a boolean somebody set by hand, and its own
comment called it a placeholder until purchase orders existed. They exist now, in
`purchase_order_lines`, so «is this on order» is computed from the orders
themselves (`procurement.reads.ordered_codes`) and the flag is dropped. A stored
flag has to be cleared by somebody, and one nobody cleared kept a material reading
«Заказан» long after the spool was on the shelf.

**The downgrade re-adds that column NOT NULL DEFAULT false and backfills
nothing**, deliberately. The truth now lives in the order lines, and reconstructing
the boolean from them would write a value for rows whose real answer this schema
never held — the same call 0023 records for `prepared_plates.copies`. A farm that
downgrades gets the column back empty and must set it again by hand, which is what
it was doing before this revision anyway.

`purchase_receipts` is a table rather than a `received_quantity` counter on the
line. A counter answers "how much came" and destroys the two facts the purchasing
screen is built on: *when* it came, and *what it cost that day*. A partial
delivery followed by the rest is two arrivals at two prices, and a year of those
rows is «Цены по ключевым позициям».

The delete rules follow what each row means. `purchase_order_lines.order_id` and
`purchase_receipts.line_id` CASCADE — a line has no meaning without its order.
`purchase_orders.supplier_id` RESTRICTs, because deleting a supplier must not
erase the record of what was bought from it; retiring one is `suppliers.is_active`
instead. `purchase_receipts.material_lot_id` and `.received_by` SET NULL — a spool
written off, or a person who left, must not delete the record that a delivery
happened.

The enum values are spelled out below rather than imported from
`procurement.policies`. A migration must keep meaning what it meant on the day it
ran, and an import would let a rename years from now rewrite history; 0019's
docstring makes the same argument at length.

`po_number_seq` is created and dropped explicitly. `Sequence(..., metadata=...)`
is not emitted by `create_table`, so `downgrade base` would otherwise leave a
sequence behind and ADR-0008's clean-downgrade rule would fail — the reason 0005
creates `order_number_seq` by hand.

Revision ID: 0024_procurement
Revises: 0023_prepared_plate_copies
Created: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_procurement"
down_revision: str | None = "0023_prepared_plate_copies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The six stages plus cancelled, as literals. See the docstring on why these are
#: not imported from `PurchaseStatus`.
_STATUSES = ("draft", "approved", "paid", "in_transit", "receiving", "stored", "cancelled")

#: All five purchasable classes, declared now though only `material` can be
#: received into stock by this build. Declaring them together means adding
#: receiving for the next class is a service change rather than a migration that
#: rewrites a CHECK on a table with live orders in it.
_KINDS = ("material", "printer", "spare_part", "packaging", "post_consumable")


def _predicate(column: str, values: tuple[str, ...]) -> str:
    literals = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({literals})"


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS po_number_seq START WITH 1")
    op.create_table(
        "suppliers",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kinds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("code", name="uq_suppliers_code"),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*_STATUSES, name="purchasestatus", native_enum=False, length=40),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("expected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receiving_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _predicate("status", _STATUSES), name=op.f("ck_purchase_orders_status_enum")
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_purchase_orders_supplier_id_suppliers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_orders")),
        sa.UniqueConstraint("number", name="uq_purchase_orders_number"),
    )
    op.create_index(
        "ix_purchase_orders_status_created_at",
        "purchase_orders",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"], unique=False
    )
    op.create_table(
        "purchase_order_lines",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(*_KINDS, name="purchasablekind", native_enum=False, length=40),
            nullable=False,
        ),
        sa.Column("item_code", sa.String(length=120), nullable=False),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _predicate("kind", _KINDS), name=op.f("ck_purchase_order_lines_kind_enum")
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_purchase_order_lines_purchase_line_quantity_positive"),
        ),
        sa.CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name=op.f("ck_purchase_order_lines_purchase_line_price_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["purchase_orders.id"],
            name=op.f("fk_purchase_order_lines_order_id_purchase_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_lines")),
    )
    op.create_index(
        "ix_purchase_order_lines_kind_item_code",
        "purchase_order_lines",
        ["kind", "item_code"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_order_lines_order_id", "purchase_order_lines", ["order_id"], unique=False
    )
    op.create_table(
        "purchase_receipts",
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("unit_price_paid", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("lot_number", sa.String(length=80), nullable=True),
        sa.Column("material_lot_id", sa.Uuid(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_purchase_receipts_purchase_receipt_quantity_positive"),
        ),
        sa.CheckConstraint(
            "unit_price_paid IS NULL OR unit_price_paid >= 0",
            name=op.f("ck_purchase_receipts_purchase_receipt_price_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["purchase_order_lines.id"],
            name=op.f("fk_purchase_receipts_line_id_purchase_order_lines"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_lot_id"],
            ["material_lots.id"],
            name=op.f("fk_purchase_receipts_material_lot_id_material_lots"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["received_by"],
            ["users.id"],
            name=op.f("fk_purchase_receipts_received_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_receipts")),
    )
    op.create_index("ix_purchase_receipts_line_id", "purchase_receipts", ["line_id"], unique=False)
    op.create_index(
        "ix_purchase_receipts_material_lot_id",
        "purchase_receipts",
        ["material_lot_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_receipts_received_by", "purchase_receipts", ["received_by"], unique=False
    )
    op.drop_column("material_specs", "has_open_order")


def downgrade() -> None:
    # Re-added with a server default so the rows already there have something, then
    # the default is dropped to match the column 0001 created. Nothing is
    # backfilled: see the module docstring. Every spec reads "not on order"
    # afterwards, which is where the farm was before anybody set the flag by hand.
    op.add_column(
        "material_specs",
        sa.Column("has_open_order", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("material_specs", "has_open_order", server_default=None)
    op.drop_index("ix_purchase_receipts_received_by", table_name="purchase_receipts")
    op.drop_index("ix_purchase_receipts_material_lot_id", table_name="purchase_receipts")
    op.drop_index("ix_purchase_receipts_line_id", table_name="purchase_receipts")
    op.drop_table("purchase_receipts")
    op.drop_index("ix_purchase_order_lines_order_id", table_name="purchase_order_lines")
    op.drop_index("ix_purchase_order_lines_kind_item_code", table_name="purchase_order_lines")
    op.drop_table("purchase_order_lines")
    op.drop_index("ix_purchase_orders_supplier_id", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_status_created_at", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_table("suppliers")
    op.execute("DROP SEQUENCE IF EXISTS po_number_seq")
