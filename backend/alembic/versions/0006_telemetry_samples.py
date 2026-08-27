"""telemetry history, partitioned by month from the first row

`printers.last_telemetry` holds one reading and is overwritten every poll. That is
the right shape for the live view and it was the only shape the fleet had, which
left three already-promised things impossible: telemetry retention and rollups
(ROADMAP phase 3), the dashboard's twelve-hour schedule, and phase 6's P&L drawn
from measured electricity rather than a guessed rate.

**Why partitioned before there is a single row.** At the default five-second poll,
fifty printers produce roughly 315 million rows a year — two orders of magnitude
more than the rest of the schema put together. Creating the table partitioned costs
nothing today. Converting a 300-million-row table to a partitioned one later means
building a copy, moving the data and swapping the two, with the writes stopped for
the duration. The asymmetry is the entire argument, and it is also why this is not
deferred to the slice that first reads the data.

Retention then becomes ``DROP TABLE telemetry_samples_2026_03`` — a catalogue
operation, constant time whatever the row count — rather than a ``DELETE`` that
would run for hours, hold locks throughout and leave bloat only ``VACUUM FULL``
reclaims. See :mod:`printorian.contexts.fleet.retention`.

Revision ID: 0006_telemetry_samples
Revises: 0005_schema_hardening
Created: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_telemetry_samples"
down_revision: str | None = "0005_schema_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "telemetry_samples"

#: Months provisioned by the migration itself. The maintenance worker keeps the
#: window rolling from here; this only has to cover the gap between deploying and
#: the worker's first sweep.
_BOOTSTRAP_MONTHS = 3


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            printer_id UUID NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            state VARCHAR(40) NOT NULL,
            job_handle VARCHAR(200),
            progress_percent INTEGER,
            layer_current INTEGER,
            layer_total INTEGER,
            remaining_minutes NUMERIC(10, 2),
            nozzle_temp_c NUMERIC(6, 2),
            bed_temp_c NUMERIC(6, 2),
            error_code VARCHAR(120),
            -- PostgreSQL requires the partition key in every unique constraint, so
            -- the key is (id, created_at) rather than id alone.
            CONSTRAINT pk_{TABLE} PRIMARY KEY (id, created_at),
            -- No CHECK on `state`, matching every other enum column in the schema.
            -- `enum_column` explains why they have none; adding one only here would
            -- read as drift on every `alembic check` and enforce nothing the others
            -- enforce. (Superseded: 0019 gives every enum column one, this table's
            -- included, after fixing the naming collision that was the real cause.)
            CONSTRAINT ck_{TABLE}_progress_range
                CHECK (progress_percent IS NULL OR (progress_percent BETWEEN 0 AND 100))
        ) PARTITION BY RANGE (created_at)
        """
    )

    # Indexes on a partitioned parent are propagated to every partition, existing
    # and future, so this is declared once and never revisited.
    op.execute(f"CREATE INDEX ix_{TABLE}_printer_id_created_at ON {TABLE} (printer_id, created_at)")

    # The safety net. Provisioning is meant to stay ahead of the data, but a row
    # with nowhere to go is a *failed insert* — the farm would stop recording what
    # its printers are doing because a cron job did not run. Anything landing here
    # is a defect the maintenance sweep reports; it is not a place data should live.
    op.execute(f"CREATE TABLE {TABLE}_default PARTITION OF {TABLE} DEFAULT")

    _bootstrap_partitions()


def downgrade() -> None:
    # Dropping the parent takes every partition with it.
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")


def _bootstrap_partitions() -> None:
    """Create the current month and the next few, in the database's own clock.

    Generated in SQL rather than in Python so the boundaries follow the server's
    time zone handling, which is what the partition constraint is evaluated in.
    Migrations also have no business reading the migrating machine's wall clock.
    """
    op.execute(
        f"""
        DO $$
        DECLARE
            start_month DATE := date_trunc('month', now())::date;
            i INTEGER;
            lower_bound DATE;
            upper_bound DATE;
            partition_name TEXT;
        BEGIN
            FOR i IN 0..{_BOOTSTRAP_MONTHS} LOOP
                lower_bound := start_month + (i || ' month')::interval;
                upper_bound := start_month + ((i + 1) || ' month')::interval;
                partition_name := format('{TABLE}_%s', to_char(lower_bound, 'YYYY_MM'));
                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS %I PARTITION OF {TABLE} '
                    'FOR VALUES FROM (%L) TO (%L)',
                    partition_name, lower_bound, upper_bound
                );
            END LOOP;
        END $$;
        """
    )
