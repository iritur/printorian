"""The model library: catalogue entries and the materials they are offered in.

Two tables. `catalog_models` is the editorial layer over geometry that already
exists in `model_assets` — this part is worth offering, in these materials, at this
difficulty — rather than a second copy of the mesh's own facts.

`catalog_model_materials` is the one multi-valued field the catalogue *filters*
on, which is why it is a table rather than a JSON column: the facet is
OR-within-group, and as JSON that is a containment operator that exists in
PostgreSQL and not in the SQLite the tests run on.

The measured columns are all nullable on purpose. Null means "nobody has printed
this yet", which is what lets the storefront label an estimate as an estimate
instead of presenting a prediction as a fact (ADR-0007's rule, applied to the
catalogue).

Revision ID: 0008_catalog_models
Revises: 0007_model_assets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_catalog_models"
down_revision: str | None = "0007_model_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_models",
        # The public identifier: it appears in a URL a customer may share, and
        # `MDL-0412` is not a thing anyone types.
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=2000), nullable=False),
        # Title, code and tags folded to lower case in Python. SQL case folding
        # is not portable over Cyrillic — SQLite's `lower()` is ASCII-only — and
        # this catalogue is written in Russian.
        sa.Column("search_text", sa.String(length=2400), nullable=False),
        # `native_enum=False` throughout, matching `core.db.enum_column`: these
        # stay VARCHAR, so adding a member is an ordinary migration rather than an
        # ALTER TYPE that cannot run inside a transaction.
        sa.Column(
            "category",
            sa.Enum(
                "func",
                "case",
                "mech",
                "org",
                "decor",
                name="modelcategory",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "size_class",
            sa.Enum("s", "m", "l", name="sizeclass", native_enum=False, length=40),
            nullable=False,
        ),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("multicolor", sa.Boolean(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("model_asset_id", sa.Uuid(), nullable=False),
        sa.Column("license", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        # Sum and count rather than an average, so a new rating is an increment
        # instead of a read-modify-write two reviewers can race on.
        sa.Column("rating_sum", sa.Integer(), nullable=False),
        sa.Column("rating_count", sa.Integer(), nullable=False),
        sa.Column("print_count", sa.Integer(), nullable=False),
        # -- the measured claim; null until a job has actually succeeded
        sa.Column("last_printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_print_minutes", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("last_print_grams", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("last_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("last_printer_name", sa.String(length=120), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview", postgresql.JSONB(), nullable=False),
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
            # RESTRICT: deleting a mesh that a published catalogue entry points at
            # should fail loudly rather than silently empty the shop window.
            ["model_asset_id"],
            ["model_assets.id"],
            name=op.f("fk_catalog_models_model_asset_id_model_assets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_models")),
        sa.UniqueConstraint("slug", name="uq_catalog_models_slug"),
        sa.CheckConstraint(
            "difficulty BETWEEN 0 AND 10", name=op.f("ck_catalog_models_difficulty_range")
        ),
        sa.CheckConstraint(
            "print_count >= 0", name=op.f("ck_catalog_models_print_count_non_negative")
        ),
        sa.CheckConstraint(
            "rating_count >= 0", name=op.f("ck_catalog_models_rating_count_non_negative")
        ),
        sa.CheckConstraint(
            "rating_sum >= 0", name=op.f("ck_catalog_models_rating_sum_non_negative")
        ),
        sa.CheckConstraint(
            "last_print_minutes IS NULL OR last_print_minutes >= 0",
            name=op.f("ck_catalog_models_last_print_minutes_non_negative"),
        ),
        sa.CheckConstraint(
            "last_print_grams IS NULL OR last_print_grams >= 0",
            name=op.f("ck_catalog_models_last_print_grams_non_negative"),
        ),
        sa.CheckConstraint(
            "last_price IS NULL OR last_price >= 0",
            name=op.f("ck_catalog_models_last_price_non_negative"),
        ),
    )
    # Every query this screen makes filters on `is_published` first, so one
    # composite index rather than three single-column ones.
    op.create_index(
        "ix_catalog_models_published",
        "catalog_models",
        ["is_published", "category", "size_class"],
    )
    op.create_index("ix_catalog_models_asset", "catalog_models", ["model_asset_id"])

    op.create_table(
        "catalog_model_materials",
        sa.Column("model_id", sa.Uuid(), nullable=False),
        # Deliberately not a foreign key to `material_specs`: the catalogue says
        # what a model is *suitable for*, which stays true while the shop is out of
        # stock and after a spec is retired.
        sa.Column("material_code", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["catalog_models.id"],
            name=op.f("fk_catalog_model_materials_model_id_catalog_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "model_id", "material_code", name=op.f("pk_catalog_model_materials")
        ),
    )
    # "Which models are offered in PETG?" — the facet's own question, asked from
    # the material side.
    op.create_index("ix_catalog_model_materials_code", "catalog_model_materials", ["material_code"])


def downgrade() -> None:
    op.drop_index("ix_catalog_model_materials_code", table_name="catalog_model_materials")
    op.drop_table("catalog_model_materials")
    op.drop_index("ix_catalog_models_asset", table_name="catalog_models")
    op.drop_index("ix_catalog_models_published", table_name="catalog_models")
    op.drop_table("catalog_models")
