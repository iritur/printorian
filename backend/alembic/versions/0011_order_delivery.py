"""Where an order goes, and how.

Backfilled as collection, which is what every existing order effectively was:
before this the delivery panel did not exist, so nobody had stated an address.
That is also why the columns arrive with a server default and then lose it — the
default exists to fill the rows already there, not to be a rule for new ones.

Revision ID: 0011_order_delivery
Revises: 0010_material_suitability
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_order_delivery"
down_revision: str | None = "0010_material_suitability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: column, type, backfill value
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object], str], ...] = (
    ("delivery_method", sa.String(length=16), "pickup"),
    ("delivery_city", sa.String(length=120), ""),
    ("delivery_postcode", sa.String(length=20), ""),
    ("delivery_address", sa.String(length=400), ""),
)


def upgrade() -> None:
    for name, kind, default in _COLUMNS:
        op.add_column(
            "orders",
            sa.Column(name, kind, nullable=False, server_default=default),
        )
        # Dropped once the existing rows are filled: the application supplies the
        # value from here on, and a lingering default would let a bug write a row
        # with no delivery at all and have the database quietly accept it.
        op.alter_column("orders", name, server_default=None)

    op.add_column(
        "orders",
        sa.Column("notify_on_progress", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("orders", "notify_on_progress", server_default=None)


def downgrade() -> None:
    op.drop_column("orders", "notify_on_progress")
    for name, _kind, _default in reversed(_COLUMNS):
        op.drop_column("orders", name)
