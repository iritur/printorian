"""The catalogue's remaining spec bars, and who drew the model.

`difficulty` shipped in 0008 as the one editorial 0–10 rating. The kit's model
popup shows six of them — difficulty, strength, accuracy, speed, supports,
post-processing — because "is this hard to print" and "will it hold" are
different questions and a reader comparing two parts needs both.

All six are judgements, not measurements: nothing in the geometry says whether a
part needs supports. They default to 0, which the screen renders as an empty bar
meaning *not yet assessed* rather than *worst possible*.

Revision ID: 0009_catalog_spec_bars
Revises: 0008_catalog_models
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_catalog_spec_bars"
down_revision: str | None = "0008_catalog_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BARS = ("strength", "accuracy", "speed", "supports", "postprocessing")


def upgrade() -> None:
    for bar in _BARS:
        # The server default exists only to backfill existing rows, which a NOT
        # NULL column needs. It is dropped immediately below: the ORM declares no
        # server default, and leaving one here makes `alembic check` report drift
        # forever after.
        op.add_column(
            "catalog_models",
            sa.Column(bar, sa.Integer(), nullable=False, server_default="0"),
        )
        op.alter_column("catalog_models", bar, server_default=None)
        # `op.f()` marks the name as final. Without it the metadata naming
        # convention prefixes it a second time, and the constraint lands as
        # `ck_catalog_models_ck_catalog_models_strength_range` — which the ORM then
        # correctly reports as a missing constraint.
        op.create_check_constraint(
            op.f(f"ck_catalog_models_{bar}_range"),
            "catalog_models",
            f"{bar} BETWEEN 0 AND 10",
        )

    op.add_column(
        "catalog_models",
        sa.Column("author", sa.String(length=120), nullable=False, server_default=""),
    )
    op.alter_column("catalog_models", "author", server_default=None)


def downgrade() -> None:
    op.drop_column("catalog_models", "author")
    for bar in reversed(_BARS):
        op.drop_constraint(op.f(f"ck_catalog_models_{bar}_range"), "catalog_models", type_="check")
        op.drop_column("catalog_models", bar)
