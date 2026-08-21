"""hourly telemetry rollups, and the retention they unlock

`telemetry_samples` has had no reader since it was created. That is why
`telemetry_retention_days` defaulted to `0`: dropping a partition is irreversible,
and raw samples were the only copy of what the farm measured. This table is the
second copy, and the default changes in the same commit.

**One row per printer per UTC hour.** 720 raw samples collapse into one row, so
fifty printers write about 438 thousand rows a year here against 315 million there.
That is the whole argument for the grain: hourly is small enough to keep for ever
and detailed enough for the two figures the dashboard exists to show — utilisation,
and the load map whose own docstring says a farm idle every night has capacity
nobody is selling "and that is invisible in a daily total".

**Deliberately not partitioned**, and this is the interesting difference from 0006.
Partitioning bought `telemetry_samples` an instant retention drop on a table two
orders of magnitude larger than everything else. Here there is no retention to make
instant, the yearly row count is smaller than several tables that are ordinary, and
every child would need adding to `alembic/env.py`'s `telemetry_samples_` filter
dance for nothing. An ordinary table is the right answer when the argument that
justified the extraordinary one does not apply.

**The natural key is the primary key.** ``(printer_id, bucket_start)`` is what the
upsert conflicts on and what every reader joins by, so a surrogate `id` would be a
column nothing references — and would have to be minted in SQL, by a different
scheme than the UUIDv7 every other id in this schema gets. `bucket_start` gets its
own index because the primary key leads with the printer and cannot answer "the
whole farm, this window".

No foreign key on `printer_id`, for the same reason `telemetry_samples` has none:
retiring a machine must not rewrite or delete the record of what it was measured
doing. `tests/test_schema_contracts.py` carries that decision in writing.

Revision ID: 0017_metric_rollups
Revises: 0016_packaging
Created: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_metric_rollups"
down_revision: str | None = "0016_packaging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Numeric(9, 2) throughout: seconds within an hour need four digits, and matching
#: precision across the nine columns is what keeps `alembic check` — which compares
#: types — from reporting drift on arithmetic that is identical.
_SECONDS = sa.Numeric(precision=9, scale=2)

#: One per `PrinterState`. Written out rather than generated, because a migration is
#: a historical record: it must keep describing the table it built even after the
#: enum gains a ninth member.
_STATE_COLUMNS = (
    "offline_seconds",
    "idle_seconds",
    "preparing_seconds",
    "printing_seconds",
    "paused_seconds",
    "finished_seconds",
    "error_seconds",
    "maintenance_seconds",
)


def upgrade() -> None:
    op.create_table(
        "metric_rollups",
        sa.Column("printer_id", sa.Uuid(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("observed_seconds", _SECONDS, nullable=False),
        *(sa.Column(name, _SECONDS, nullable=False) for name in _STATE_COLUMNS),
        sa.Column("state_changes", sa.Integer(), nullable=False),
        sa.Column("error_sample_count", sa.Integer(), nullable=False),
        sa.Column("error_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("nozzle_temp_avg_c", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("nozzle_temp_max_c", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("bed_temp_avg_c", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("bed_temp_max_c", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.CheckConstraint(
            "error_sample_count >= 0",
            name=op.f("ck_metric_rollups_error_sample_count_non_negative"),
        ),
        sa.CheckConstraint(
            "observed_seconds BETWEEN 0 AND 3600",
            name=op.f("ck_metric_rollups_observed_seconds_within_the_hour"),
        ),
        sa.CheckConstraint(
            "offline_seconds >= 0 AND idle_seconds >= 0 AND preparing_seconds >= 0 "
            "AND printing_seconds >= 0 AND paused_seconds >= 0 AND finished_seconds >= 0 "
            "AND error_seconds >= 0 AND maintenance_seconds >= 0",
            name=op.f("ck_metric_rollups_state_seconds_non_negative"),
        ),
        sa.CheckConstraint(
            "sample_count >= 0", name=op.f("ck_metric_rollups_sample_count_non_negative")
        ),
        sa.CheckConstraint(
            "state_changes >= 0", name=op.f("ck_metric_rollups_state_changes_non_negative")
        ),
        sa.PrimaryKeyConstraint("printer_id", "bucket_start", name=op.f("pk_metric_rollups")),
    )
    op.create_index("ix_metric_rollups_bucket_start", "metric_rollups", ["bucket_start"])


def downgrade() -> None:
    op.drop_index("ix_metric_rollups_bucket_start", table_name="metric_rollups")
    op.drop_table("metric_rollups")
