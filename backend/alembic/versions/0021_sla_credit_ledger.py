"""the SLA credit gets a ledger, because a column is not a record

`refresh_sla_credit` overwrote `orders.sla_credit` in place. The arithmetic was
right and nothing was kept: no `order_events` row, no subscriber behind
`SlaCreditAccrued` — the bus is in-process and its sinks persist nothing — and no
prior value anywhere. On a path where money leaves the farm through
`PaymentsService.refund_sla_credit`, and where revenue is reported net of the
credit in `ordering/finance.py`, "why was this customer credited 4 200 ₽" had no
answer beyond the number currently in the column.

**Its own table rather than an `order_events` row**, which is what issue #75
proposed. `OrderView` eagerly loads `Order.events` on every read, `table()`
included — which loads them for every row on the page. The credit moves on every
sweep: at the default `sla_sweep_seconds=300` a `standard` promise accrues about
0.25 ₽ every five minutes for the six days it takes to reach the 30% cap, so one
late order produces 1 728 movements and a page of twenty would carry
thirty-four thousand event rows in a single response. The ledger is written far
more often than an order's history and is read by query, so it is deliberately
not reachable from `Order`.

Nothing is backfilled and nothing can be. The previous values were never written
down; a ledger opening with rows reconstructed from today's column would be an
invented history, which is the one thing ADR-0007 forbids outright. An order that
was already late when this ran gets its first entry on the next movement, and the
absence before it is honest.

No separate index on `order_id`: the `(order_id, sequence)` unique constraint is
an index whose leading column is `order_id`, so both the read path and the
`CASCADE` check are served by it. `order_events` carries the same note.

Revision ID: 0021_sla_credit_ledger
Revises: 0020_order_decay_terms
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_sla_credit_ledger"
down_revision: str | None = "0020_order_decay_terms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sla_credit_entries",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("previous", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("credit", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decay_policy", sa.String(length=32), nullable=False),
        sa.Column("decay_percent_per_day", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("decay_grace_seconds", sa.Integer(), nullable=True),
        sa.Column("decay_max_percent", sa.Numeric(precision=5, scale=2), nullable=True),
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
            "credit <> previous", name=op.f("ck_sla_credit_entries_credit_actually_moved")
        ),
        sa.CheckConstraint("credit >= 0", name=op.f("ck_sla_credit_entries_credit_non_negative")),
        sa.CheckConstraint(
            "previous >= 0", name=op.f("ck_sla_credit_entries_previous_non_negative")
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_sla_credit_entries_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_sla_credit_entries_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sla_credit_entries")),
        sa.UniqueConstraint("order_id", "sequence", name="uq_sla_credit_entries_order_id_sequence"),
    )


def downgrade() -> None:
    op.drop_table("sla_credit_entries")
