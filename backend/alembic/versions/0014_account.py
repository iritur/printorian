"""The customer's own record: addresses, notification preferences, profile fields.

Four changes, all additive and all safe on a populated database:

* `users` gains `phone` and `customer_kind`;
* `sessions` gains `client_ip` and `last_seen_at`. `last_seen_at` is nullable
  rather than backfilled to `created_at` — a session issued before this migration
  has no recorded last use, and claiming one would be inventing a figure the
  security screen presents as measured;
* `addresses`, per customer, at most one default;
* `notification_prefs`, one row per customer, created on first write.

The added columns arrive with a server default and immediately lose it, the same
way `0011_order_delivery` did: the default exists to fill the rows already there,
not to be a rule for new ones. Leaving it would let a bug insert a row with no
value and have the database quietly supply one — and `test_migrations` compares
the result against the ORM, which declares its defaults in Python.

Revision ID: 0014_account
Revises: 0013_journal_subscribers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_account"
down_revision: str | None = "0013_journal_subscribers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: table, column, type, backfill value
_ADDED: tuple[tuple[str, str, sa.types.TypeEngine[object], str], ...] = (
    ("users", "phone", sa.String(length=40), ""),
    ("users", "customer_kind", sa.String(length=16), "person"),
    ("sessions", "client_ip", sa.String(length=45), ""),
)


def upgrade() -> None:
    for table, name, kind, default in _ADDED:
        op.add_column(table, sa.Column(name, kind, nullable=False, server_default=default))
        op.alter_column(table, name, server_default=None)

    op.add_column("sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "addresses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("recipient", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("postcode", sa.String(length=20), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("address", sa.String(length=400), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
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
            ["user_id"],
            ["users.id"],
            name=op.f("fk_addresses_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_addresses")),
    )
    op.create_index(op.f("ix_addresses_user_id"), "addresses", ["user_id"])
    op.create_index(op.f("ix_addresses_user_id_is_default"), "addresses", ["user_id", "is_default"])

    op.create_table(
        "notification_prefs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("on_paid", sa.Boolean(), nullable=False),
        sa.Column("on_print_started", sa.Boolean(), nullable=False),
        sa.Column("on_every_stage", sa.Boolean(), nullable=False),
        sa.Column("on_shipped", sa.Boolean(), nullable=False),
        sa.Column("on_new_sign_in", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_prefs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_notification_prefs")),
    )


def downgrade() -> None:
    op.drop_table("notification_prefs")
    op.drop_index(op.f("ix_addresses_user_id_is_default"), table_name="addresses")
    op.drop_index(op.f("ix_addresses_user_id"), table_name="addresses")
    op.drop_table("addresses")
    op.drop_column("sessions", "last_seen_at")
    for table, name, _kind, _default in reversed(_ADDED):
        op.drop_column(table, name)
