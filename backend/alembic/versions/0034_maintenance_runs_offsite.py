"""maintenance runs: the off-site copy leaves a stamp too

`deploy/offsite.sh` copies the blob store and the backups to a restic repository
off the farm (issue #16), and `printorian-offsite.service` writes a row here when
it completes. The CHECK is the contract that unit's `psql` insert is held to, so
it widens by one literal.

Downgrade deletes the `offsite` rows before restoring the narrower CHECK — the
constraint cannot come back while rows it forbids exist. What is lost is only
the history of when the copy last succeeded; the copies themselves live in the
restic repository and are untouched.

Revision ID: 0034_maintenance_runs_offsite
Revises: 0033_maintenance_runs
Created: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034_maintenance_runs_offsite"
down_revision: str | None = "0033_maintenance_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_maintenance_runs_kind_known"), "maintenance_runs", type_="check")
    op.create_check_constraint(
        "kind_known", "maintenance_runs", "kind IN ('backup', 'restore_drill', 'offsite')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM maintenance_runs WHERE kind = 'offsite'")
    op.drop_constraint(op.f("ck_maintenance_runs_kind_known"), "maintenance_runs", type_="check")
    op.create_check_constraint(
        "kind_known", "maintenance_runs", "kind IN ('backup', 'restore_drill')"
    )
