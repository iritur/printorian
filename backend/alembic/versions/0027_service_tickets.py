"""service tickets: the work behind a failure, and the work that has nothing to do with one

Two tables and a sequence. Issue #33's second slice: the first was the failure
record (0024), the *measurement* of a machine being down; this is the *work* —
raised by a person or by the sweep from a driver-reported failure, walked through
its steps, closed. `contexts/service/tickets.py` says why the two are different
rows and why a closed ticket is not evidence a machine came back.

**The sequence** is `sv_number_seq`, created and dropped explicitly for the reason
0026 gives about `po_number_seq`: `Sequence(..., metadata=...)` registers it for
`create_all` and nothing else, so a migration that relied on that would leave the
sequence behind on downgrade and ADR-0008's clean-downgrade rule would fail.

**Three enum CHECKs**, per 0019, with their members spelled out as literals rather
than imported from `contexts.service.policies` — a migration keeps meaning what it
meant on the day it ran.

**The CHECKs on time.** `started_at >= opened_at` and `closed_at >= opened_at`:
the desk refuses the same with a code, and that refusal only exists while the
application is the writer. A hand-run UPDATE would otherwise put a negative
elapsed time on the board.

`printer_id` is RESTRICT, copying `printer_failures.printer_id` and for the same
reason. `failure_id` is SET NULL rather than CASCADE: the failure is the
measurement and outlives the work. The two `users` keys are SET NULL, as
everywhere a person is named. Steps CASCADE with their ticket — a step is not a
record of anything without the ticket it belongs to.

Revision ID: 0027_service_tickets
Revises: 0026_procurement
Created: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_service_tickets"
down_revision: str | None = "0026_procurement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS sv_number_seq START WITH 1")

    op.create_table(
        "service_tickets",
        sa.Column("number", sa.String(length=16), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "install",
                "repair",
                "maintenance",
                "material_load",
                "move",
                name="ticketkind",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open", "in_progress", "closed", name="ticketstatus", native_enum=False, length=40
            ),
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum("driver", "person", name="failureorigin", native_enum=False, length=40),
            nullable=False,
        ),
        sa.Column("printer_id", sa.Uuid(), nullable=True),
        sa.Column("failure_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("norm_minutes", sa.Integer(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_by", sa.Uuid(), nullable=True),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
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
            "kind IN ('install', 'repair', 'maintenance', 'material_load', 'move')",
            name=op.f("ck_service_tickets_kind_enum"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'closed')",
            name=op.f("ck_service_tickets_status_enum"),
        ),
        sa.CheckConstraint(
            "origin IN ('driver', 'person')",
            name=op.f("ck_service_tickets_origin_enum"),
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= opened_at",
            name=op.f("ck_service_tickets_started_after_opened"),
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= opened_at",
            name=op.f("ck_service_tickets_closed_after_opened"),
        ),
        sa.CheckConstraint(
            "norm_minutes IS NULL OR norm_minutes > 0",
            name=op.f("ck_service_tickets_norm_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_service_tickets_assignee_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["failure_id"],
            ["printer_failures.id"],
            name=op.f("fk_service_tickets_failure_id_printer_failures"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by"],
            ["users.id"],
            name=op.f("fk_service_tickets_opened_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_service_tickets_printer_id_printers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_tickets")),
        sa.UniqueConstraint("number", name="uq_service_tickets_number"),
    )
    op.create_index(
        "ix_service_tickets_assignee_id", "service_tickets", ["assignee_id"], unique=False
    )
    op.create_index("ix_service_tickets_closed_at", "service_tickets", ["closed_at"], unique=False)
    op.create_index(
        "ix_service_tickets_failure_id", "service_tickets", ["failure_id"], unique=False
    )
    op.create_index("ix_service_tickets_opened_by", "service_tickets", ["opened_by"], unique=False)
    op.create_index(
        "ix_service_tickets_printer_id", "service_tickets", ["printer_id"], unique=False
    )
    op.create_index(
        "ix_service_tickets_status_opened",
        "service_tickets",
        ["status", "opened_at"],
        unique=False,
    )

    op.create_table(
        "service_ticket_steps",
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("norm_minutes", sa.Integer(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("position >= 1", name=op.f("ck_service_ticket_steps_position_positive")),
        sa.CheckConstraint(
            "norm_minutes IS NULL OR norm_minutes > 0",
            name=op.f("ck_service_ticket_steps_step_norm_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["done_by"],
            ["users.id"],
            name=op.f("fk_service_ticket_steps_done_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["service_tickets.id"],
            name=op.f("fk_service_ticket_steps_ticket_id_service_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_ticket_steps")),
        sa.UniqueConstraint("ticket_id", "position", name="uq_service_ticket_steps_position"),
    )
    op.create_index(
        "ix_service_ticket_steps_done_by", "service_ticket_steps", ["done_by"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_service_ticket_steps_done_by", table_name="service_ticket_steps")
    op.drop_table("service_ticket_steps")
    for name in (
        "ix_service_tickets_status_opened",
        "ix_service_tickets_printer_id",
        "ix_service_tickets_opened_by",
        "ix_service_tickets_failure_id",
        "ix_service_tickets_closed_at",
        "ix_service_tickets_assignee_id",
    ):
        op.drop_index(name, table_name="service_tickets")
    op.drop_table("service_tickets")
    op.execute("DROP SEQUENCE IF EXISTS sv_number_seq")
