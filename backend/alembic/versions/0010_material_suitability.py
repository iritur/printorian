"""How well each material suits a model, for the catalogue's «Подходящие материалы».

`catalog_model_materials` recorded only *which* materials a model is offered in.
The kit's popup asks three more things of each row: how good a fit it is, which one
is recommended, and any caveat — «Не для улицы» on PLA for an outdoor bracket.

All three are editorial. Nothing in the geometry knows that a part will sit in the
sun, so they are stored rather than derived. The remaining two columns of that
table — Δ price and stock — are *not* here: they are facts about pricing and
inventory, composed at read time from those contexts rather than copied into this
one where they would go stale.

Revision ID: 0010_material_suitability
Revises: 0009_catalog_spec_bars
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_material_suitability"
down_revision: str | None = "0009_catalog_spec_bars"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Server defaults backfill the existing rows, then go: the ORM declares none,
    # and leaving them would make `alembic check` report drift forever.
    op.add_column(
        "catalog_model_materials",
        sa.Column(
            "suitability",
            sa.Enum(
                "excellent",
                "good",
                "limited",
                name="suitability",
                native_enum=False,
                length=40,
            ),
            nullable=False,
            server_default="good",
        ),
    )
    op.alter_column("catalog_model_materials", "suitability", server_default=None)

    op.add_column(
        "catalog_model_materials",
        sa.Column("note", sa.String(length=60), nullable=False, server_default=""),
    )
    op.alter_column("catalog_model_materials", "note", server_default=None)

    op.add_column(
        "catalog_model_materials",
        sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("catalog_model_materials", "is_recommended", server_default=None)


def downgrade() -> None:
    op.drop_column("catalog_model_materials", "is_recommended")
    op.drop_column("catalog_model_materials", "note")
    op.drop_column("catalog_model_materials", "suitability")
