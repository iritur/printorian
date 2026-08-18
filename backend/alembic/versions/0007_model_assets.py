"""model assets: uploaded geometry as a first-class record

The chain payment → prep → slice → dispatch was broken in the middle. An uploaded
mesh was analysed for its volume and discarded, `PreparedPlate.storage_path` was a
column nothing wrote, and `plate_key`'s `model_hash` — the input the whole plate
cache is keyed on — was never computed. So the console had nothing to offer an
engineer for download and the server had no plate bytes to send to a printer.

This is the database half of closing that. Bytes live on disk behind
`core.storage`; what lands here is the reference, the digest and the measurements.

Three changes:

1. **`model_assets`** — one row per distinct upload, unique on its SHA-256, so
   re-uploading a file the farm already holds is free and two customers uploading
   the same part share a prepared plate.
2. **`order_lines.model_asset_id`** (`RESTRICT`) — what a line was priced from.
   The delete rule is the whole of retention's protection: the database refuses to
   collect geometry an order still has to print, so the sweep never has to ask
   `ordering` anything.
3. **`prepared_plates.model_asset_id`** (`SET NULL`) and **`content_sha256`** — the
   link back to the source mesh, and the address of the plate's own bytes. Null
   until an engineer uploads the sliced file, so the dispatcher can tell a plate
   with numbers from a plate with a file.

Revision ID: 0007_model_assets
Revises: 0006_telemetry_samples
Created: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_model_assets"
down_revision: str | None = "0006_telemetry_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_assets",
        # The content address: hex SHA-256 of the bytes as uploaded. Also the
        # object's name in the store and `plate_key`'s `model_hash`.
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=300), nullable=False),
        sa.Column(
            "format",
            sa.Enum("stl", "3mf", "other", name="modelformat", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        # Relative to the store root, so moving the storage directory — or
        # restoring onto a differently-laid-out box — does not invalidate every row.
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        # Geometry a query might filter on lives in columns; the full analysis,
        # warnings and all, sits beside them. "Which models fit a 256 mm bed" should
        # not require reading JSON for every row.
        sa.Column("triangle_count", sa.Integer(), nullable=False),
        sa.Column("volume_cm3", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("width_mm", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("depth_mm", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("height_mm", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_watertight", sa.Boolean(), nullable=False),
        sa.Column("mesh", postgresql.JSONB(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        # Retention counts from here, not from `created_at`: a model reprinted every
        # month is never collected while an experiment from last year is.
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
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
            ["uploaded_by"],
            ["users.id"],
            name=op.f("fk_model_assets_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_assets")),
        sa.UniqueConstraint("sha256", name="uq_model_assets_sha256"),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_model_assets_size_non_negative")),
        sa.CheckConstraint(
            "triangle_count >= 0", name=op.f("ck_model_assets_triangle_count_non_negative")
        ),
        sa.CheckConstraint("volume_cm3 >= 0", name=op.f("ck_model_assets_volume_non_negative")),
    )
    op.create_index("ix_model_assets_last_used_at", "model_assets", ["last_used_at"])
    op.create_index("ix_model_assets_uploaded_by", "model_assets", ["uploaded_by"])

    # -- order lines ------------------------------------------------------
    op.add_column("order_lines", sa.Column("model_asset_id", sa.Uuid(), nullable=True))
    op.create_index("ix_order_lines_model_asset_id", "order_lines", ["model_asset_id"])
    # RESTRICT: the database itself refuses to collect geometry an order still has
    # to print. Retention relies on this rather than on a query, which would both
    # cross a context boundary and race with an order placed mid-sweep.
    op.create_foreign_key(
        op.f("fk_order_lines_model_asset_id_model_assets"),
        "order_lines",
        "model_assets",
        ["model_asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # -- prepared plates --------------------------------------------------
    op.add_column("prepared_plates", sa.Column("model_asset_id", sa.Uuid(), nullable=True))
    op.add_column(
        "prepared_plates", sa.Column("content_sha256", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_prepared_plates_model_asset_id", "prepared_plates", ["model_asset_id"])
    # SET NULL, unlike order lines: `model_hash` stays on the plate and remains the
    # cache key, so a plate is still valid and findable after retention has
    # collected the mesh it came from. This link is the convenience, not the key.
    op.create_foreign_key(
        op.f("fk_prepared_plates_model_asset_id_model_assets"),
        "prepared_plates",
        "model_assets",
        ["model_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # -- print jobs -------------------------------------------------------
    # What the prep queue needs in order to offer geometry for download, and what
    # `plate_key` needs in order to find a cached plate for it.
    op.add_column("print_jobs", sa.Column("model_asset_id", sa.Uuid(), nullable=True))
    op.add_column(
        "print_jobs",
        sa.Column("model_hash", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "print_jobs",
        sa.Column("scale", sa.Numeric(precision=8, scale=4), nullable=False, server_default="1"),
    )
    op.create_index("ix_print_jobs_model_asset_id", "print_jobs", ["model_asset_id"])
    op.create_check_constraint("scale_positive", "print_jobs", "scale > 0")
    # RESTRICT, like order lines: a job still waiting to print protects the mesh it
    # is going to need.
    op.create_foreign_key(
        op.f("fk_print_jobs_model_asset_id_model_assets"),
        "print_jobs",
        "model_assets",
        ["model_asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # The server defaults existed only so the columns could be added NOT NULL to a
    # table with rows in it. The application always supplies both, and leaving them
    # would let a future insert quietly omit the cache key.
    op.alter_column("print_jobs", "model_hash", server_default=None)
    op.alter_column("print_jobs", "scale", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_print_jobs_model_asset_id_model_assets"), "print_jobs", type_="foreignkey"
    )
    op.drop_constraint(op.f("ck_print_jobs_scale_positive"), "print_jobs", type_="check")
    op.drop_index("ix_print_jobs_model_asset_id", table_name="print_jobs")
    op.drop_column("print_jobs", "scale")
    op.drop_column("print_jobs", "model_hash")
    op.drop_column("print_jobs", "model_asset_id")

    op.drop_constraint(
        op.f("fk_prepared_plates_model_asset_id_model_assets"),
        "prepared_plates",
        type_="foreignkey",
    )
    op.drop_index("ix_prepared_plates_model_asset_id", table_name="prepared_plates")
    op.drop_column("prepared_plates", "content_sha256")
    op.drop_column("prepared_plates", "model_asset_id")

    op.drop_constraint(
        op.f("fk_order_lines_model_asset_id_model_assets"), "order_lines", type_="foreignkey"
    )
    op.drop_index("ix_order_lines_model_asset_id", table_name="order_lines")
    op.drop_column("order_lines", "model_asset_id")

    op.drop_index("ix_model_assets_uploaded_by", table_name="model_assets")
    op.drop_index("ix_model_assets_last_used_at", table_name="model_assets")
    op.drop_table("model_assets")
