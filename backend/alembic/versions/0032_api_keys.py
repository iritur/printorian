"""api keys: a machine's read credential, stored as a hash

One table. Issue #29's «Ключи API»: a key is issued by a person, holds read
permissions only, and is stored as the SHA-256 of the key — the same rule
`sessions.token_hash` follows, and for the same reason: a database leak must not
hand out live credentials. `prefix` is the key's first twelve characters, kept
so the screen can tell keys apart without holding anything usable.

`owner_id` is CASCADE: a key is a credential of the person who issued it and
must not outlive the account behind it. Revocation is a timestamp rather than a
delete, so the list still says which key stopped and when.

`permissions` is JSONB holding permission codes rather than an enum array, so a
permission later removed from the matrix is skipped at resolution rather than
failing every request that presents the key.

Revision ID: 0032_api_keys
Revises: 0031_one_live_payment_per_order
Created: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032_api_keys"
down_revision: str | None = "0031_one_live_payment_per_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_api_keys_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_index("ix_api_keys_owner_id", "api_keys", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_api_keys_owner_id", table_name="api_keys")
    op.drop_table("api_keys")
