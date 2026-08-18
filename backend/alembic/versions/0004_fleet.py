"""fleet: printers, ams slots, service card

Revision ID: 0004_fleet
Revises: 0003_payments
Created: 2026-08-08 10:13:29.061414
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_fleet"
down_revision: str | None = "0003_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "printers",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("brand", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("serial", sa.String(length=120), nullable=False),
        sa.Column(
            "connection_mode",
            sa.Enum(
                "lan",
                "cloud",
                "manual",
                "mock",
                name="connectionmode",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("host", sa.String(length=120), nullable=True),
        sa.Column("access_code_encrypted", sa.String(length=500), nullable=True),
        sa.Column("build_width_mm", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("build_depth_mm", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("build_height_mm", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("nozzle_diameter_mm", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("supports_multi_material", sa.Boolean(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "offline",
                "idle",
                "preparing",
                "printing",
                "paused",
                "finished",
                "error",
                "maintenance",
                name="printerstate",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("last_telemetry", sa.JSON(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_available", sa.Boolean(), nullable=False),
        sa.Column("acquisition_cost", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("expected_lifetime_hours", sa.Integer(), nullable=False),
        sa.Column("printed_hours", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("nominal_power_kw", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_printers")),
        sa.UniqueConstraint("name", name=op.f("uq_printers_name")),
    )
    op.create_index("ix_printers_state_is_active", "printers", ["state", "is_active"], unique=False)
    op.create_table(
        "ams_slots",
        sa.Column("printer_id", sa.Uuid(), nullable=False),
        sa.Column("unit", sa.Integer(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("material_type", sa.String(length=40), nullable=True),
        sa.Column("colour_hex", sa.String(length=9), nullable=True),
        sa.Column("remaining_percent", sa.Integer(), nullable=True),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_ams_slots_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ams_slots")),
    )
    op.create_index(
        "ix_ams_slots_printer_id_unit_index",
        "ams_slots",
        ["printer_id", "unit", "index"],
        unique=False,
    )
    op.create_table(
        "service_operations",
        sa.Column("printer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "nozzle_change",
                "belt_tension",
                "lubrication",
                "bed_level",
                "filter_change",
                "deep_clean",
                name="maintenancekind",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("last_done_at_hours", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("last_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("materials_used", sa.JSON(), nullable=False),
        sa.Column("notes", sa.String(length=1000), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["printer_id"],
            ["printers.id"],
            name=op.f("fk_service_operations_printer_id_printers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_operations")),
    )


def downgrade() -> None:
    op.drop_table("service_operations")
    op.drop_index("ix_ams_slots_printer_id_unit_index", table_name="ams_slots")
    op.drop_table("ams_slots")
    op.drop_index("ix_printers_state_is_active", table_name="printers")
    op.drop_table("printers")
