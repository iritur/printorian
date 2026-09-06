"""the failure record: what broke, when it came back, and who said so

One table. Three of its constraints are the point of the migration rather than
decoration, and each is here because the application is not the only writer this
schema will ever see.

**The partial unique index.** ``uq_printer_failures_open`` allows one row per
printer with ``restored_at IS NULL``, and it is what makes `workers/service.py`
idempotent *in the database* rather than only in Python. The sweep checks for an
open failure before it inserts, but that check is a race with the next pass, with a
second worker process, and with anybody at a psql prompt. Without the index a
restarted worker mints a duplicate failure per broken machine and the reliability
figure counts one outage twice.

**The CHECK on the repair.** ``restored_at IS NULL OR restored_at >= detected_at``.
`ServiceDesk.restore` refuses the same thing with a code, and that refusal is the
half that only exists while the application is the writer. A clock that stepped
backwards, an import or a hand-run UPDATE would otherwise put a negative repair
time into MTTR, and a mean is exactly the shape that hides one bad row.

**Two enum CHECKs**, per 0019, with their members spelled out as literals rather
than imported from `contexts.service.policies`. 0019's own note gives the reason
and it holds here: a migration has to keep meaning what it meant on the day it ran,
and an import lets a rename years from now quietly rewrite history.

``printer_id`` is ``RESTRICT`` — the only foreign key on this table that refuses.
Nothing in the tree deletes a printer (the fleet retires them with ``is_active``),
so the rule costs nothing today and refuses the one deletion that would be
unrecoverable: the one that erases the evidence a machine was unreliable.
``recorded_by`` is ``SET NULL``, copying `postproduction_tasks.operator_id`; a
person leaving the farm must not take the record of what broke with them, and NULL
there already means "the sweep opened this".

No retention, deliberately, and for the reason `metric_rollups` has none: a failure
history that expires is a reliability figure that quietly improves.

Revision ID: 0024_printer_failures
Revises: 0023_prepared_plate_copies
Created: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_printer_failures"
down_revision: str | None = "0023_prepared_plate_copies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "printer_failures",
        sa.Column("printer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "origin",
            sa.Enum("driver", "person", name="failureorigin", native_enum=False, length=40),
            nullable=False,
        ),
        # Nullable, and it is the nullability that carries the meaning: a failure
        # the driver opened has no cause until a person names one, because the farm
        # holds no table mapping `bambu.print_error.{code}` to «слом филамента»
        # (ADR-0007). `other` is *named*, and different from NULL.
        sa.Column(
            "cause",
            sa.Enum(
                "filament_break",
                "nozzle_clog",
                "adhesion",
                "network",
                "ams_sensor",
                "other",
                name="failurecause",
                native_enum=False,
                length=40,
            ),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
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
            "cause IN ('filament_break', 'nozzle_clog', 'adhesion', 'network', "
            "'ams_sensor', 'other')",
            name=op.f("ck_printer_failures_cause_enum"),
        ),
        sa.CheckConstraint(
            "origin IN ('driver', 'person')",
            name=op.f("ck_printer_failures_origin_enum"),
        ),
        sa.CheckConstraint(
            "restored_at IS NULL OR restored_at >= detected_at",
            name=op.f("ck_printer_failures_restored_after_detected"),
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_printer_failures_printer_id_printers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            name=op.f("fk_printer_failures_recorded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_printer_failures")),
    )
    # "What did this machine do over this window" — every read in
    # `contexts/service/reliability.py` — and the index the RESTRICT check on
    # `printer_id` reads, which would otherwise scan the whole table on a delete.
    op.create_index(
        "ix_printer_failures_printer_detected",
        "printer_failures",
        ["printer_id", "detected_at"],
        unique=False,
    )
    # PostgreSQL does not index a foreign key for you, and this one is checked on
    # every user delete.
    op.create_index(
        "ix_printer_failures_recorded_by", "printer_failures", ["recorded_by"], unique=False
    )
    # The load-bearing one — see the module docstring.
    op.create_index(
        "uq_printer_failures_open",
        "printer_failures",
        ["printer_id"],
        unique=True,
        postgresql_where=sa.text("restored_at IS NULL"),
    )


def downgrade() -> None:
    # The indexes go with the table; dropping them first is redundant in
    # PostgreSQL and is written out anyway so `downgrade` reads as the exact
    # inverse of `upgrade` rather than as something shorter that happens to work.
    op.drop_index("uq_printer_failures_open", table_name="printer_failures")
    op.drop_index("ix_printer_failures_recorded_by", table_name="printer_failures")
    op.drop_index("ix_printer_failures_printer_detected", table_name="printer_failures")
    op.drop_table("printer_failures")
