"""stocktakes: the book on one side, what somebody counted on the other

Two tables and a sequence. Issue #35's last measurable row: «Расхождения» and
the «Инвентаризация» panel both rest on this, and neither can be reconstructed
later — a count not written down at the shelf is gone.

**`expected_grams` is a snapshot**, copied off the lot when the count opens, so
the line keeps saying what the book claimed on the day. **`counted_grams` is
nullable and never defaulted**: an uncounted line is absent, not zero, or every
shelf nobody reached would read as a shortage of everything on it (ADR-0007).
The CHECK below ties `counted_at` to it, so a count always says when it was
made. **`variance_grams`** is written at close and is exactly what the
`stock.counted` ledger row carried — stored, not recomputed, so the report does
not drift when the spool is written off afterwards.

`stocktake_lines.lot_id` is RESTRICT: a closed count's variance is the evidence
a shortage rests on. The two `users` keys and `counted_by` are SET NULL, as
every actor column here is. Lines CASCADE with their stocktake. The sequence
`st_number_seq` is created and dropped explicitly, as `sv_number_seq` was, so the
downgrade is clean (ADR-0008).

Revision ID: 0030_stocktakes
Revises: 0029_lot_dried_at
Created: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_stocktakes"
down_revision: str | None = "0029_lot_dried_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS st_number_seq START WITH 1")

    op.create_table(
        "stocktakes",
        sa.Column("number", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "closed", name="stocktakestatus", native_enum=False, length=40),
            nullable=False,
        ),
        sa.Column("zone_code", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_by", sa.Uuid(), nullable=True),
        sa.Column("closed_by", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_stocktakes_status_enum")),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= opened_at",
            name=op.f("ck_stocktakes_closed_after_opened"),
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"],
            ["users.id"],
            name=op.f("fk_stocktakes_closed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by"],
            ["users.id"],
            name=op.f("fk_stocktakes_opened_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stocktakes")),
        sa.UniqueConstraint("number", name="uq_stocktakes_number"),
    )
    op.create_index("ix_stocktakes_closed_by", "stocktakes", ["closed_by"], unique=False)
    op.create_index("ix_stocktakes_opened_by", "stocktakes", ["opened_by"], unique=False)
    op.create_index(
        "ix_stocktakes_status_opened_at", "stocktakes", ["status", "opened_at"], unique=False
    )

    op.create_table(
        "stocktake_lines",
        sa.Column("stocktake_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("family", sa.String(length=40), nullable=False),
        sa.Column("cell_address", sa.String(length=24), nullable=True),
        sa.Column("expected_grams", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("counted_grams", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counted_by", sa.Uuid(), nullable=True),
        sa.Column("variance_grams", sa.Numeric(precision=10, scale=2), nullable=True),
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
            "expected_grams >= 0", name=op.f("ck_stocktake_lines_expected_non_negative")
        ),
        sa.CheckConstraint(
            "counted_grams IS NULL OR counted_grams >= 0",
            name=op.f("ck_stocktake_lines_counted_non_negative"),
        ),
        sa.CheckConstraint(
            "(counted_grams IS NULL) = (counted_at IS NULL)",
            name=op.f("ck_stocktake_lines_counted_at_with_count"),
        ),
        sa.ForeignKeyConstraint(
            ["counted_by"],
            ["users.id"],
            name=op.f("fk_stocktake_lines_counted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["material_lots.id"],
            name=op.f("fk_stocktake_lines_lot_id_material_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stocktake_id"],
            ["stocktakes.id"],
            name=op.f("fk_stocktake_lines_stocktake_id_stocktakes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stocktake_lines")),
        sa.UniqueConstraint(
            "stocktake_id", "lot_id", name="uq_stocktake_lines_stocktake_id_lot_id"
        ),
    )
    op.create_index(
        "ix_stocktake_lines_counted_by", "stocktake_lines", ["counted_by"], unique=False
    )
    op.create_index("ix_stocktake_lines_lot_id", "stocktake_lines", ["lot_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stocktake_lines_lot_id", table_name="stocktake_lines")
    op.drop_index("ix_stocktake_lines_counted_by", table_name="stocktake_lines")
    op.drop_table("stocktake_lines")
    op.drop_index("ix_stocktakes_status_opened_at", table_name="stocktakes")
    op.drop_index("ix_stocktakes_opened_by", table_name="stocktakes")
    op.drop_index("ix_stocktakes_closed_by", table_name="stocktakes")
    op.drop_table("stocktakes")
    op.execute("DROP SEQUENCE IF EXISTS st_number_seq")
