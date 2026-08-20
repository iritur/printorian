"""An operator's shift: tasks, the instructions behind them, and what they use up.

Four tables, and two shapes worth explaining before they look like oversights.

**The keys cross context boundaries, and that is correct.** `ordering` owns
orders and `fleet` owns printers, but the import boundary is about Python
modules, not about what the database may check: there is one database and one
domain model, and `print_jobs` references `orders` exactly like this. The delete
rules differ by what the row means — an order that no longer exists has no
finishing work (``CASCADE``), while retiring a printer must not destroy the
record of what it made (``SET NULL``).

**`postproduction_task_steps` duplicates `postproduction_instruction_steps`.**
That is the point. A task step is the record of what an operator was told to do
and what it cost them; republishing the instruction must not rewrite the job
somebody is halfway through, and a norm that changed retroactively is a norm
nobody trusts.

Numbered 0015, not 0014, and still descending from 0013.

`design-kit-account-cabinet` carries a `0014_account` that also descends from
0013. Two revisions with one parent are two Alembic heads, and the one-head gate
fails on whichever branch merges second. Renumbering here does not fix that on
its own — heads are counted from the revision graph, not from filenames — but it
reserves 0014 for the account work and reduces the eventual fix to one line:
when both are on the same branch, point this at ``0014_account``.

It cannot be pointed there now. `0014_account` does not exist on this branch, and
``alembic upgrade head`` would fail to locate it before it did anything else.

Revision ID: 0015_postproduction
Revises: 0013_journal_subscribers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_postproduction"
down_revision: str | None = "0013_journal_subscribers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "postproduction_consumables",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("remaining", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("reorder_at", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "remaining >= 0",
            name=op.f("ck_postproduction_consumables_consumable_remaining_non_negative"),
        ),
        sa.CheckConstraint(
            "reorder_at >= 0",
            name=op.f("ck_postproduction_consumables_consumable_reorder_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postproduction_consumables")),
        sa.UniqueConstraint("code", name="uq_postproduction_consumables_code"),
    )
    op.create_table(
        "postproduction_operations",
        sa.Column(
            "kind",
            sa.Enum(
                "support_removal",
                "sanding",
                "priming",
                "painting",
                "polishing",
                "assembly",
                name="operationkind",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("norm_minutes_per_unit", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("cure_minutes", sa.Integer(), nullable=False),
        sa.Column("instruction_version", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "cure_minutes >= 0", name=op.f("ck_postproduction_operations_cure_non_negative")
        ),
        sa.CheckConstraint(
            "norm_minutes_per_unit > 0", name=op.f("ck_postproduction_operations_norm_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postproduction_operations")),
        sa.UniqueConstraint("kind", name="uq_postproduction_operations_kind"),
    )
    op.create_table(
        "postproduction_instruction_steps",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=True),
        sa.Column("warning", sa.String(length=1000), nullable=True),
        sa.Column("norm_minutes", sa.Numeric(precision=8, scale=2), nullable=False),
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
            "norm_minutes >= 0",
            name=op.f("ck_postproduction_instruction_steps_step_norm_non_negative"),
        ),
        sa.CheckConstraint(
            "position >= 1", name=op.f("ck_postproduction_instruction_steps_position_positive")
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["postproduction_operations.id"],
            name=op.f("fk_postproduction_instruction_steps_operation_id_postproduction_operations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postproduction_instruction_steps")),
        sa.UniqueConstraint("operation_id", "position", name="uq_instruction_step_position"),
    )
    op.create_table(
        "postproduction_tasks",
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(length=300), nullable=False),
        sa.Column("material_code", sa.String(length=80), nullable=False),
        sa.Column("colors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("printer_id", sa.Uuid(), nullable=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "waiting",
                "in_progress",
                "paused",
                "curing",
                "for_qc",
                "returned",
                "done",
                "cancelled",
                name="taskstatus",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("norm_minutes", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("instruction_version", sa.String(length=16), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column("elapsed_minutes", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("running_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cure_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("defect_code", sa.String(length=120), nullable=True),
        sa.Column("defect_note", sa.String(length=1000), nullable=True),
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
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_postproduction_tasks_attempt_positive")),
        sa.CheckConstraint(
            "elapsed_minutes >= 0", name=op.f("ck_postproduction_tasks_elapsed_non_negative")
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_postproduction_tasks_finished_after_started"),
        ),
        sa.CheckConstraint(
            "norm_minutes >= 0", name=op.f("ck_postproduction_tasks_task_norm_non_negative")
        ),
        sa.CheckConstraint("quantity >= 1", name=op.f("ck_postproduction_tasks_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["postproduction_operations.id"],
            name=op.f("fk_postproduction_tasks_operation_id_postproduction_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["users.id"],
            name=op.f("fk_postproduction_tasks_operator_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_postproduction_tasks_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_postproduction_tasks_printer_id_printers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postproduction_tasks")),
        sa.UniqueConstraint("number", name="uq_postproduction_tasks_number"),
    )
    op.create_index(
        "ix_postproduction_tasks_operator_id", "postproduction_tasks", ["operator_id"], unique=False
    )
    op.create_index(
        "ix_postproduction_tasks_operation_id",
        "postproduction_tasks",
        ["operation_id"],
        unique=False,
    )
    op.create_index(
        "ix_postproduction_tasks_order_id", "postproduction_tasks", ["order_id"], unique=False
    )
    op.create_index(
        "ix_postproduction_tasks_printer_id", "postproduction_tasks", ["printer_id"], unique=False
    )
    op.create_index(
        "ix_postproduction_tasks_status_due",
        "postproduction_tasks",
        ["status", "due_at"],
        unique=False,
    )
    op.create_table(
        "postproduction_task_steps",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=True),
        sa.Column("warning", sa.String(length=1000), nullable=True),
        sa.Column("norm_minutes", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("actual_minutes", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
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
            "actual_minutes IS NULL OR actual_minutes >= 0",
            name=op.f("ck_postproduction_task_steps_task_step_actual_non_negative"),
        ),
        sa.CheckConstraint(
            "norm_minutes >= 0",
            name=op.f("ck_postproduction_task_steps_task_step_norm_non_negative"),
        ),
        sa.CheckConstraint(
            "position >= 1", name=op.f("ck_postproduction_task_steps_task_step_position_positive")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["postproduction_tasks.id"],
            name=op.f("fk_postproduction_task_steps_task_id_postproduction_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postproduction_task_steps")),
        sa.UniqueConstraint("task_id", "position", name="uq_task_step_position"),
    )


def downgrade() -> None:
    op.drop_table("postproduction_task_steps")
    op.drop_index("ix_postproduction_tasks_status_due", table_name="postproduction_tasks")
    op.drop_index("ix_postproduction_tasks_printer_id", table_name="postproduction_tasks")
    op.drop_index("ix_postproduction_tasks_order_id", table_name="postproduction_tasks")
    op.drop_index("ix_postproduction_tasks_operation_id", table_name="postproduction_tasks")
    op.drop_index("ix_postproduction_tasks_operator_id", table_name="postproduction_tasks")
    op.drop_table("postproduction_tasks")
    op.drop_table("postproduction_instruction_steps")
    op.drop_table("postproduction_operations")
    op.drop_table("postproduction_consumables")
