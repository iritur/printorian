"""payment holds: money that arrived for an order not waiting for it

Security review §5(d). A settlement on an order that was already cancelled — or
already paid by another attempt — was recorded and then forgotten. The owner's
decision is to hold it for a person, and this is where the hold lives: one row
per payment, written in the settlement's own transaction
(`contexts/payments/holding.py`).

`payment_id` cascades because a hold describes its payment and nothing else;
`released_by` is set null because a person leaving must not erase the record
that somebody decided to keep the money.

Revision ID: 0035_payment_holds
Revises: 0034_maintenance_runs_offsite
Created: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035_payment_holds"
down_revision: str | None = "0034_maintenance_runs_offsite"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_holds",
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("order_status", sa.String(length=40), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("release_note", sa.String(length=500), nullable=False),
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
            ["payment_id"],
            ["payments.id"],
            name=op.f("fk_payment_holds_payment_id_payments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["released_by"],
            ["users.id"],
            name=op.f("fk_payment_holds_released_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_holds")),
        sa.UniqueConstraint("payment_id", name="uq_payment_holds_payment_id"),
    )
    op.create_index(
        "ix_payment_holds_released_by", "payment_holds", ["released_by"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_payment_holds_released_by", table_name="payment_holds")
    op.drop_table("payment_holds")
