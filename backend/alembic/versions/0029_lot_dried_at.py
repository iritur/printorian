"""lot dried_at: the one instant a spool's drying state is computed from

One nullable column on `material_lots`. Issue #35: `inventory.require_drying`
and `inventory.drying_valid_hours` sat in the settings catalogue with nothing
reading them, because no row said when a spool was last dried.

**Nullable, no default, no backfill.** Every spool the farm holds today has
never been marked, and the read path reports that as *unknown* — not as expired,
which would claim a measurement nobody took, and not as dried on the day of this
migration, which would be the invented number (ADR-0007). The state itself is
never stored: it is this instant against the clock and the setting at read time,
which is why this is a column and not an enum.

No index. The column is read per cell — a handful of rows already selected by
`cell_id` — and never searched by.

Revision ID: 0029_lot_dried_at
Revises: 0028_shipments
Created: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_lot_dried_at"
down_revision: str | None = "0028_shipments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("material_lots", sa.Column("dried_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("material_lots", "dried_at")
