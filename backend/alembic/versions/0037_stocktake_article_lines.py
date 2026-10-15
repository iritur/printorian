"""stocktake article lines: the count covers packaging, consumables and parts too

Issue #35's last piece. A stocktake lined up only spools (`stocktake_lines`, in
grams); the shared stock from migration 0036 sits on the same shelves and has to
be counted with them. `StocktakeLine`'s shape in the article's own unit, for the
reason `article_movements` is its own table rather than a widened
`material_movements` (`contexts/inventory/stocktake_models.py`).

Purely additive: a new table, nothing existing altered, so a rollback of the
application needs no rollback of the database.

Revision ID: 0037_stocktake_article_lines
Revises: 0036_stock_articles
Created: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037_stocktake_article_lines"
down_revision: str | None = "0036_stock_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stocktake_article_lines",
        sa.Column("stocktake_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("cell_address", sa.String(length=24), nullable=True),
        sa.Column("expected", sa.Numeric(12, 2), nullable=False),
        sa.Column("counted", sa.Numeric(12, 2), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counted_by", sa.Uuid(), nullable=True),
        sa.Column("variance", sa.Numeric(12, 2), nullable=True),
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
            "expected >= 0", name=op.f("ck_stocktake_article_lines_expected_non_negative")
        ),
        sa.CheckConstraint(
            "counted IS NULL OR counted >= 0",
            name=op.f("ck_stocktake_article_lines_counted_non_negative"),
        ),
        sa.CheckConstraint(
            "(counted IS NULL) = (counted_at IS NULL)",
            name=op.f("ck_stocktake_article_lines_counted_at_with_count"),
        ),
        sa.ForeignKeyConstraint(
            ["stocktake_id"],
            ["stocktakes.id"],
            name=op.f("fk_stocktake_article_lines_stocktake_id_stocktakes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["stock_articles.id"],
            name=op.f("fk_stocktake_article_lines_article_id_stock_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["counted_by"],
            ["users.id"],
            name=op.f("fk_stocktake_article_lines_counted_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stocktake_article_lines")),
        sa.UniqueConstraint(
            "stocktake_id",
            "article_id",
            name="uq_stocktake_article_lines_stocktake_id_article_id",
        ),
    )
    op.create_index(
        "ix_stocktake_article_lines_article_id",
        "stocktake_article_lines",
        ["article_id"],
        unique=False,
    )
    op.create_index(
        "ix_stocktake_article_lines_counted_by",
        "stocktake_article_lines",
        ["counted_by"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stocktake_article_lines_counted_by", table_name="stocktake_article_lines")
    op.drop_index("ix_stocktake_article_lines_article_id", table_name="stocktake_article_lines")
    op.drop_table("stocktake_article_lines")
