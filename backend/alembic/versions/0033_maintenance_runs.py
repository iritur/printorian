"""maintenance runs: the row a successful backup or restore drill leaves behind

One table, append-only. INFRASTRUCTURE §5's two backup stamps could not be
served because neither job left anything machine-readable; each now writes a row
here on success — `scripts/backup.sh` through `psql`, `scripts/restore_drill.py`
through SQLAlchemy — and `GET /metrics` reads the newest of each kind.

A row per *success* only, never a failure: the failures that matter (disk full,
container gone, timer never fired) are exactly the ones a job cannot report, so
the signal is the newest success going stale.

`kind` is a string with a CHECK rather than an enum column, because half its
writers are a shell script and the CHECK is the contract it is held to. The
predicate is spelled with literals, as the 0019 CHECKs are.

`id` carries a server default (`gen_random_uuid()`) so `psql` can insert without
inventing one; the ORM still supplies its own UUIDv7 and the default is unused
there.

Revision ID: 0033_maintenance_runs
Revises: 0032_api_keys
Created: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033_maintenance_runs"
down_revision: str | None = "0032_api_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "maintenance_runs",
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("detail", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
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
            "kind IN ('backup', 'restore_drill')", name=op.f("ck_maintenance_runs_kind_known")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_runs")),
    )
    op.create_index(
        "ix_maintenance_runs_kind_finished_at",
        "maintenance_runs",
        ["kind", "finished_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_maintenance_runs_kind_finished_at", table_name="maintenance_runs")
    op.drop_table("maintenance_runs")
