"""give a spool a cell, and every move of it a row nothing can overwrite

`MaterialLot` answered "where is this spool" with five columns it overwrites in
place. That is the right shape for the scheduler's question and the wrong shape
for every other one: mounting a spool destroyed the record of the cell it came
out of, and "who took 400 g off this reel and why" had no answer at all once the
column had been rewritten. The field said *where*; nothing said *how it got
there*.

Three tables and one column. `storage_zones` is a named area with its measured
conditions, `storage_cells` is one addressable place inside it, and
`material_movements` is the append-only ledger.

**`capacity_lots` is nullable with no server default and no backfill.** A cell
whose capacity nobody has declared has no fill percentage. Not 0%, which reads as
empty about a cell holding four spools; and not the 100% a default of `1` would
produce for every cell holding one. NULL is "not measured" (ADR-0007, and the
same shape `prepared_plates.copies` took in 0023).

**`material_lots.cell_id` backfills nothing from `shelf`.** `shelf` is an
unvalidated `String(60)` typed by hand — "стеллаж 2", "у окна", "A1?" — and there
is no honest mapping from it to an address. Inventing one would put every spool in
the farm in a place the farm never said it was in. `shelf` therefore stays exactly
where it is, alone, and `policies.Location` holds the one rule saying the cell
wins when both are set.

**The ledger's two sides are copied text, not foreign keys.** Pointing
`from_address`/`to_address` at `storage_cells` leaves two bad options: `SET NULL`,
which erases the one answer the row exists to give the moment a cell is retired,
or `RESTRICT`, which makes retiring a cell impossible for as long as the history
is kept. `material_movements.lot_id` is `RESTRICT` for the matching reason — a
stock history that can be rewritten by deleting the spool it describes is not a
record — and the cost of that is written at the column in `movements.py`.

`actor_id` is `SET NULL` after `order_events.actor_id`: removing a member of staff
must not delete the record of what moved, and *who* is the one part of it the farm
accepts losing.

No separate index on `material_movements.lot_id`: the `(lot_id, sequence)` unique
constraint is an index whose leading column is `lot_id`, so the read path and the
`RESTRICT` check are both served by it. `sla_credit_entries` carries the same note.
`actor_id` and `material_lots.cell_id` have no such cover and get their own —
`test_schema_contracts.test_every_foreign_key_is_indexed` is the gate that said so,
and the cost it is about is a sequential scan on every retirement.

Revision ID: 0025_storage_cells_and_movements
Revises: 0024_printer_failures
Created: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_storage_cells_and_movements"
down_revision: str | None = "0024_printer_failures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _location_kind() -> sa.Enum:
    """The same `VARCHAR`-backed enum `material_lots.location_kind` already uses.

    Spelled out rather than imported from the models: a migration has to keep
    saying what it said on the day it ran, and an import would make it follow the
    enum wherever it goes next.
    """
    return sa.Enum(
        "stock",
        "printer",
        "dryer",
        "consumed",
        name="locationkind",
        native_enum=False,
        length=40,
    )


def upgrade() -> None:
    op.create_table(
        "storage_zones",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("temp_c", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("humidity_percent", sa.Numeric(precision=5, scale=1), nullable=True),
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
            "humidity_percent IS NULL OR (humidity_percent >= 0 AND humidity_percent <= 100)",
            name=op.f("ck_storage_zones_humidity_is_a_percentage"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_zones")),
        sa.UniqueConstraint("code", name=op.f("uq_storage_zones_code")),
    )
    op.create_table(
        "storage_cells",
        sa.Column("zone_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(length=24), nullable=False),
        sa.Column("capacity_lots", sa.Integer(), nullable=True),
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
        # NULL has to be admitted explicitly, or "nobody has declared a capacity"
        # becomes unrepresentable the moment the constraint is added.
        sa.CheckConstraint(
            "capacity_lots IS NULL OR capacity_lots >= 1",
            name=op.f("ck_storage_cells_capacity_positive_when_declared"),
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["storage_zones.id"],
            name=op.f("fk_storage_cells_zone_id_storage_zones"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_cells")),
        # Unique farm-wide rather than per zone: the address is what gets written on
        # a spool and read back by somebody standing in the aisle, and they have no
        # zone in their hand to disambiguate it with.
        sa.UniqueConstraint("address", name=op.f("uq_storage_cells_address")),
    )
    op.create_index("ix_storage_cells_zone_id", "storage_cells", ["zone_id"], unique=False)

    op.create_table(
        "material_movements",
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("grams", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("remaining_after", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("from_kind", _location_kind(), nullable=True),
        sa.Column("from_address", sa.String(length=60), nullable=True),
        sa.Column("to_kind", _location_kind(), nullable=True),
        sa.Column("to_address", sa.String(length=60), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
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
            "from_kind IN ('stock', 'printer', 'dryer', 'consumed')",
            name=op.f("ck_material_movements_from_kind_enum"),
        ),
        sa.CheckConstraint("grams >= 0", name=op.f("ck_material_movements_grams_non_negative")),
        sa.CheckConstraint(
            "remaining_after >= 0",
            name=op.f("ck_material_movements_remaining_after_non_negative"),
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_material_movements_sequence_positive")),
        sa.CheckConstraint(
            "to_kind IN ('stock', 'printer', 'dryer', 'consumed')",
            name=op.f("ck_material_movements_to_kind_enum"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_material_movements_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["material_lots.id"],
            name=op.f("fk_material_movements_lot_id_material_lots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_movements")),
        sa.UniqueConstraint("lot_id", "sequence", name="uq_material_movements_lot_id_sequence"),
    )

    op.create_index(
        "ix_material_movements_actor_id", "material_movements", ["actor_id"], unique=False
    )

    op.add_column("material_lots", sa.Column("cell_id", sa.Uuid(), nullable=True))
    op.create_index("ix_material_lots_cell_id", "material_lots", ["cell_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_material_lots_cell_id_storage_cells"),
        "material_lots",
        "storage_cells",
        ["cell_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Undo it in dependency order — an untested rollback is not a plan (ADR-0008).

    The column and its key go first: `material_lots` points at `storage_cells`, and
    `storage_cells` cannot be dropped underneath it.
    """
    op.drop_constraint(
        op.f("fk_material_lots_cell_id_storage_cells"), "material_lots", type_="foreignkey"
    )
    op.drop_index("ix_material_lots_cell_id", table_name="material_lots")
    op.drop_column("material_lots", "cell_id")
    op.drop_index("ix_material_movements_actor_id", table_name="material_movements")
    op.drop_table("material_movements")
    op.drop_index("ix_storage_cells_zone_id", table_name="storage_cells")
    op.drop_table("storage_cells")
    op.drop_table("storage_zones")
