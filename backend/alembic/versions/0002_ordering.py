"""ordering: orders, lines and events

Revision ID: 0002_ordering
Revises: 0001_initial
Created: 2026-08-06 20:50:26.667257
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_ordering"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "awaiting_payment",
                "paid",
                "prep",
                "price_review",
                "queued",
                "printing",
                "post_production",
                "quality_check",
                "packing",
                "shipped",
                "completed",
                "cancelled",
                "refunded",
                name="orderstatus",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("customer_email", sa.String(length=320), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("price_breakdown", sa.JSON(), nullable=False),
        sa.Column("rate_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=16), nullable=False),
        sa.Column("promised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decay_policy", sa.String(length=32), nullable=False),
        sa.Column("sla_credit", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["users.id"],
            name=op.f("fk_orders_customer_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("number", name=op.f("uq_orders_number")),
    )
    op.create_index(
        "ix_orders_customer_id_created_at", "orders", ["customer_id", "created_at"], unique=False
    )
    op.create_index("ix_orders_status_created_at", "orders", ["status", "created_at"], unique=False)
    op.create_table(
        "order_events",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "from_status",
            sa.Enum(
                "draft",
                "awaiting_payment",
                "paid",
                "prep",
                "price_review",
                "queued",
                "printing",
                "post_production",
                "quality_check",
                "packing",
                "shipped",
                "completed",
                "cancelled",
                "refunded",
                name="orderstatus",
                native_enum=False,
                length=40,
            ),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum(
                "draft",
                "awaiting_payment",
                "paid",
                "prep",
                "price_review",
                "queued",
                "printing",
                "post_production",
                "quality_check",
                "packing",
                "shipped",
                "completed",
                "cancelled",
                "refunded",
                name="orderstatus",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=80), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_events_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )
    op.create_index(
        "ix_order_events_order_id_created_at",
        "order_events",
        ["order_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "order_lines",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(length=300), nullable=False),
        sa.Column("material_code", sa.String(length=80), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("scale", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("rush", sa.Boolean(), nullable=False),
        sa.Column("colors", sa.JSON(), nullable=False),
        sa.Column("finishes", sa.JSON(), nullable=False),
        sa.Column("estimate_source", sa.String(length=32), nullable=False),
        sa.Column("estimated_minutes", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("estimated_grams", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("mesh", sa.JSON(), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=14, scale=2), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_lines_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_lines")),
    )


def downgrade() -> None:
    op.drop_table("order_lines")
    op.drop_index("ix_order_events_order_id_created_at", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_orders_status_created_at", table_name="orders")
    op.drop_index("ix_orders_customer_id_created_at", table_name="orders")
    op.drop_table("orders")
