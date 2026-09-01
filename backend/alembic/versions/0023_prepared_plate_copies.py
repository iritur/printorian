"""record how many copies a plate holds, so intake can stop assuming

`PreparedPlate` carried minutes, grams and an opaque `layout_hash`, and nowhere
said how many parts were on the bed. Two places were nevertheless asserting it:
`production.plates.attach_plate` writes the plate's minutes and grams onto the job
as its *whole* work, and `pricing.reprice.prepared_cost` divides those same totals
by the line's quantity to get a per-unit figure. Both are claims about a layout,
and the automatic intake path (#58) was making them unattended.

A `PrintJob` is one plate holding a whole line's work, so a multi-up plate is the
normal cache entry: the first order for two keychains leaves a two-up plate behind.
The repeat order for *one* then repriced against half the bed, came out 4.26% over
the quote — inside ADR-0013's band — and queued itself. Two printed, one shipped,
and the variance table recorded an accurate estimate.

**Nullable, with no server default and no backfill.** Every row already in this
table was sliced by an engineer who was never asked, so there is no measured value
to write. A `1` would be the number that makes the common case attach, invented
for exactly the rows that must not (CLAUDE.md §1). NULL means "not measured", and
`workers/cached_plates.py` refuses it; those plates keep working on every path
where a person can look at the bed.

Revision ID: 0023_prepared_plate_copies
Revises: 0022_plate_model_hash_index
Created: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_prepared_plate_copies"
down_revision: str | None = "0022_plate_model_hash_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("prepared_plates", sa.Column("copies", sa.Integer(), nullable=True))
    # NULL has to be admitted explicitly, or every pre-existing row fails the
    # constraint the moment it is added: "not measured" is the state this column
    # exists to be able to hold.
    op.create_check_constraint(
        "copies_positive", "prepared_plates", "copies IS NULL OR copies >= 1"
    )


def downgrade() -> None:
    op.drop_constraint("copies_positive", "prepared_plates", type_="check")
    op.drop_column("prepared_plates", "copies")
