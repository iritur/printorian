"""initial schema: identity and inventory

Revision ID: 0001_initial
Revises:
Created: 2026-08-05 20:23:43.846870
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "material_specs",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("family", sa.String(length=40), nullable=False),
        sa.Column("form", sa.String(length=20), nullable=False),
        sa.Column("color_name", sa.String(length=80), nullable=False),
        sa.Column("color_hex", sa.String(length=9), nullable=False),
        sa.Column("density_g_per_cm3", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("purchase_price_per_1000m", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("sell_price_per_gram", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("tensile_mpa", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("hdt_c", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("is_flexible", sa.Boolean(), nullable=False),
        sa.Column("is_outdoor_safe", sa.Boolean(), nullable=False),
        sa.Column("nozzle_temp_range", sa.String(length=40), nullable=True),
        sa.Column("bed_temp_range", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("has_open_order", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_specs")),
        sa.UniqueConstraint("code", name=op.f("uq_material_specs_code")),
    )
    op.create_index(
        "ix_material_specs_family_active", "material_specs", ["family", "is_active"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "customer",
                "operator",
                "engineer",
                "manager",
                "owner",
                name="role",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "material_lots",
        sa.Column("spec_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("initial_grams", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("remaining_grams", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("purchase_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("lot_number", sa.String(length=80), nullable=True),
        sa.Column(
            "location_kind",
            sa.Enum(
                "stock",
                "printer",
                "dryer",
                "consumed",
                name="locationkind",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("shelf", sa.String(length=60), nullable=True),
        sa.Column("printer_id", sa.String(length=80), nullable=True),
        sa.Column("ams_unit", sa.Integer(), nullable=True),
        sa.Column("ams_slot", sa.Integer(), nullable=True),
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
            ["spec_id"],
            ["material_specs.id"],
            name=op.f("fk_material_lots_spec_id_material_specs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_lots")),
    )
    op.create_index(
        "ix_material_lots_spec_id_location_kind",
        "material_lots",
        ["spec_id", "location_kind"],
        unique=False,
    )
    op.create_table(
        "sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
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
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index(
        "ix_sessions_user_id_expires_at", "sessions", ["user_id", "expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_user_id_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_material_lots_spec_id_location_kind", table_name="material_lots")
    op.drop_table("material_lots")
    op.drop_table("users")
    op.drop_index("ix_material_specs_family_active", table_name="material_specs")
    op.drop_table("material_specs")
