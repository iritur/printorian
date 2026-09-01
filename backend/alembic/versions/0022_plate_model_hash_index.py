"""index prepared plates by geometry, so intake can ask without a profile

`workers/intake.py` now tries the plate cache for every line of every paid order,
and it cannot use the unique `plate_key` index to do it: the key includes the
printer profile, and an order has none — the profile is the engineer's choice at
the slicer, made after the point intake runs.

So the lookup is by `model_hash` plus status, and the exact key is reconstructed
per candidate in Python (`PlateLibrary.find_unambiguous`). Unindexed, that is a
sequential scan of every plate the farm has ever produced, once per order line,
every `intake_sweep_seconds` — a table that only grows, on the path between a
customer's payment and a machine starting.

No index on `(model_hash, status)`: a hash selects a handful of rows at most —
one per profile the farm slices for — and the status filter runs on those. The
composite would be wider on every insert and save nothing worth measuring.

The revision id is shorter than the name of what it does, because
`alembic_version.version_num` is `varchar(32)` and Alembic writes it there rather
than truncating: a longer one fails the whole upgrade at the last statement, with
a message about a string, and `tests/test_migrations.py` is the only thing that
notices. It noticed.

Revision ID: 0022_plate_model_hash_index
Revises: 0021_sla_credit_ledger
Created: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_plate_model_hash_index"
down_revision: str | None = "0021_sla_credit_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_prepared_plates_model_hash", "prepared_plates", ["model_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_prepared_plates_model_hash", table_name="prepared_plates")
