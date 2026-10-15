"""stock articles: packaging, consumables and spare parts become stock

Issue #35, the owner's decision: one shared stock model. `stock_articles` holds
an item and how many the farm has, in its own unit and at most one cell;
`article_movements` is its append-only ledger, the shape of `material_movements`
in the article's unit (`contexts/inventory/article_models.py`).

**Expand, not contract (INFRASTRUCTURE §4.5).** The two columns this replaces —
`packaging_tara.stock` and `postproduction_consumables.remaining` — are copied
here and **left in place**. The new code treats the article as the truth and
keeps `packaging_tara.stock` written beside it, so rolling the application back
never needs the database rolled back with it. A later migration drops both once
no release reads them.

The backfill gives each copied item one opening row in its ledger
(`stock.counted`, "opening balance"), so its history starts from the number it
inherited rather than from nothing. `unit_cost` is copied from the packing
catalogue's own price where one was set (`price > 0`); a consumable never had a
price, so it starts null, and a later receipt fills it.

`purchase_receipts.stock_article_id` is the receipt's pointer to the article it
filled — the twin of `material_lot_id` — so a received carton can be traced to
the shelf it went to. `SET NULL`, for the reason that key gives.

Revision ID: 0036_stock_articles
Revises: 0035_payment_holds
Created: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_stock_articles"
down_revision: str | None = "0035_payment_holds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _entity_columns() -> list[sa.Column[object]]:
    """`Entity`'s three columns, built fresh for each table that needs them."""
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "stock_articles",
        sa.Column(
            "kind",
            sa.Enum(
                "packaging",
                "post_consumable",
                "spare_part",
                name="articlekind",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("on_hand", sa.Numeric(12, 2), nullable=False),
        sa.Column("reorder_at", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("cell_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_entity_columns(),
        sa.CheckConstraint(
            "kind IN ('packaging', 'post_consumable', 'spare_part')",
            name=op.f("ck_stock_articles_kind_enum"),
        ),
        sa.CheckConstraint("on_hand >= 0", name=op.f("ck_stock_articles_on_hand_non_negative")),
        sa.CheckConstraint(
            "reorder_at >= 0", name=op.f("ck_stock_articles_reorder_at_non_negative")
        ),
        sa.CheckConstraint(
            "unit_cost IS NULL OR unit_cost >= 0",
            name=op.f("ck_stock_articles_unit_cost_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["cell_id"],
            ["storage_cells.id"],
            name=op.f("fk_stock_articles_cell_id_storage_cells"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_articles")),
        sa.UniqueConstraint("kind", "code", name="uq_stock_articles_kind_code"),
    )
    op.create_index("ix_stock_articles_cell_id", "stock_articles", ["cell_id"], unique=False)

    op.create_table(
        "article_movements",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("on_hand_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("from_address", sa.String(length=60), nullable=True),
        sa.Column("to_address", sa.String(length=60), nullable=True),
        sa.Column("reference", sa.String(length=80), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        *_entity_columns(),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_article_movements_sequence_positive")),
        sa.CheckConstraint(
            "quantity >= 0", name=op.f("ck_article_movements_quantity_non_negative")
        ),
        sa.CheckConstraint(
            "on_hand_after >= 0", name=op.f("ck_article_movements_on_hand_after_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["stock_articles.id"],
            name=op.f("fk_article_movements_article_id_stock_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_article_movements_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_article_movements")),
        sa.UniqueConstraint(
            "article_id", "sequence", name="uq_article_movements_article_id_sequence"
        ),
    )
    op.create_index(
        "ix_article_movements_actor_id", "article_movements", ["actor_id"], unique=False
    )

    op.add_column("purchase_receipts", sa.Column("stock_article_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_purchase_receipts_stock_article_id_stock_articles"),
        "purchase_receipts",
        "stock_articles",
        ["stock_article_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_purchase_receipts_stock_article_id",
        "purchase_receipts",
        ["stock_article_id"],
        unique=False,
    )

    # -- the backfill ------------------------------------------------------
    op.execute(
        """
        INSERT INTO stock_articles
            (id, kind, code, name, unit, on_hand, reorder_at, unit_cost, is_active)
        SELECT gen_random_uuid(), 'packaging', code, name, unit, stock, reorder_at,
               CASE WHEN price > 0 THEN price END, is_active
        FROM packaging_tara
        """
    )
    op.execute(
        """
        INSERT INTO stock_articles
            (id, kind, code, name, unit, on_hand, reorder_at, unit_cost, is_active)
        SELECT gen_random_uuid(), 'post_consumable', code, name, unit, remaining, reorder_at,
               NULL, is_active
        FROM postproduction_consumables
        """
    )
    op.execute(
        """
        INSERT INTO article_movements
            (id, article_id, sequence, reason, quantity, on_hand_after, at, note)
        SELECT gen_random_uuid(), id, 1, 'stock.counted', on_hand, on_hand, now(),
               'opening balance, migration 0036'
        FROM stock_articles
        WHERE on_hand > 0
        """
    )


def downgrade() -> None:
    op.drop_index("ix_purchase_receipts_stock_article_id", table_name="purchase_receipts")
    op.drop_constraint(
        op.f("fk_purchase_receipts_stock_article_id_stock_articles"),
        "purchase_receipts",
        type_="foreignkey",
    )
    op.drop_column("purchase_receipts", "stock_article_id")
    op.drop_index("ix_article_movements_actor_id", table_name="article_movements")
    op.drop_table("article_movements")
    op.drop_index("ix_stock_articles_cell_id", table_name="stock_articles")
    op.drop_table("stock_articles")
