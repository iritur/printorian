"""The journal's one table.

A new table, so nothing to backfill: every column is NOT NULL from the start
because there are no existing rows for a default to rescue.

`section` is a VARCHAR with a check-free enum rather than a PostgreSQL ENUM type
(see `core.db.enum_column`) — adding a sixth section should be an ordinary
migration, not an ALTER TYPE that locks the table.

Revision ID: 0012_journal
Revises: 0011_order_delivery
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_journal"
down_revision: str | None = "0011_order_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_posts",
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("lede", sa.String(length=600), nullable=False),
        sa.Column("excerpt", sa.String(length=600), nullable=False),
        sa.Column(
            "section",
            sa.Enum(
                "cost",
                "materials",
                "fleet",
                "architecture",
                "postprocessing",
                name="section",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("author", sa.String(length=160), nullable=False),
        sa.Column("data_note", sa.String(length=200), nullable=False),
        sa.Column("read_minutes", sa.Integer(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_text", sa.String(length=1000), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_posts")),
        sa.UniqueConstraint("number", name=op.f("uq_journal_posts_number")),
        sa.UniqueConstraint("slug", name=op.f("uq_journal_posts_slug")),
    )
    op.create_index(
        "ix_journal_posts_published", "journal_posts", ["is_published", "number"], unique=False
    )
    op.create_index("ix_journal_posts_section", "journal_posts", ["section"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_journal_posts_section", table_name="journal_posts")
    op.drop_index("ix_journal_posts_published", table_name="journal_posts")
    op.drop_table("journal_posts")
