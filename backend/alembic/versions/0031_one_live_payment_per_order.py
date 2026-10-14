"""one live payment per order, enforced by the database

A partial unique index, `uq_payments_one_live_per_order`: at most one row per
order whose status is `created` or `pending`. Issue #27, second pass of the
security review: `PaymentsService.start` checked for a live payment before it
inserted one, and that check is a race with a double click. The loser of the
race registered a second payment at the gateway — two confirmation pages, both
payable, and nothing on the farm's side that would notice the second charge.
With the index the loser's insert fails before the gateway is called, and
`start` hands back the winner.

**Refuses to run over data the index would reject**, rather than deleting or
cancelling rows to make room. Choosing which of two live payments to cancel is
a decision about somebody's money, and a migration is the wrong place to make it
silently. Nothing has taken a real payment yet (`docs/RUNBOOK-PAYMENTS.md`), so
on every database this has to run against the check finds nothing; if it ever
does, the error names the orders and the decision goes to a person.

The predicate is spelled with the enum's values as literals, as the 0019 CHECKs
are, so the migration keeps meaning what it meant on the day it ran.

Revision ID: 0031_one_live_payment_per_order
Revises: 0030_stocktakes
Created: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_one_live_payment_per_order"
down_revision: str | None = "0030_stocktakes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIVE = "status IN ('created', 'pending')"


def upgrade() -> None:
    duplicated = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT order_id FROM payments WHERE {_LIVE} "
                "GROUP BY order_id HAVING count(*) > 1 ORDER BY order_id"
            )
        )
        .scalars()
        .all()
    )
    if duplicated:
        raise RuntimeError(
            "orders with more than one live payment — cancel all but one by hand, "
            f"then rerun: {', '.join(str(order_id) for order_id in duplicated)}"
        )

    op.create_index(
        "uq_payments_one_live_per_order",
        "payments",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(_LIVE),
    )


def downgrade() -> None:
    op.drop_index("uq_payments_one_live_per_order", table_name="payments")
