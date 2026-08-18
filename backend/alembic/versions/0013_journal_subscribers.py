"""Who asked to hear about new reports.

A table of its own rather than a flag on `users`: most people who want a farm's
journal have no account, and requiring one to read a blog would be exactly the
friction the storefront exists to remove.

The primary key is the address itself. Subscribing twice is one subscription, and
letting the database say so is simpler than a uniqueness check that races.

Revision ID: 0013_journal_subscribers
Revises: 0012_journal
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_journal_subscribers"
down_revision: str | None = "0012_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_subscribers",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("email", name=op.f("pk_journal_subscribers")),
        sa.UniqueConstraint("token", name=op.f("uq_journal_subscribers_token")),
    )


def downgrade() -> None:
    op.drop_table("journal_subscribers")
