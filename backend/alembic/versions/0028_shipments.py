"""shipments: the parcel after the post, its pinned promise, and every event since

Two tables. Issue #36: a parcel used to end at `packaging_tasks.shipped_at` with a
`carrier_code` and nothing else — no zone, no promise, no arrival, and therefore
no «В срок» and no «Точность» that anybody measured.

**The promise is pinned on the row.** `zone_code` and `promised_days` are the
zone the delivery postcode fell in *when the parcel shipped*, taken from the
order's own rate snapshot. A zone edited afterwards changes the next promise
and never this one, so a carrier cannot be made punctual by moving the goalposts.

**Arrival is an event, not a flip.** `delivered_at` is written by the `delivered`
event alone, and the CHECK below refuses an arrival before the dispatch — the
service refuses it with a code, and that refusal exists only while the
application is the writer.

**Three enum CHECKs**, per 0019, with their members as literals so the migration
keeps meaning what it meant on the day it ran.

`order_id` is RESTRICT: an order delete that erased how its parcel travelled
would erase the evidence the carrier scorecard rests on. `pack_task_id` is
SET NULL: the packing row is the shipment's origin, not its identity. Events
CASCADE with their shipment. No retention on either table — an accuracy figure
whose history expires is one that quietly improves.

Revision ID: 0028_shipments
Revises: 0027_service_tickets
Created: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_shipments"
down_revision: str | None = "0027_service_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("pack_task_id", sa.Uuid(), nullable=True),
        sa.Column("carrier_code", sa.String(length=40), nullable=False),
        sa.Column("zone_code", sa.String(length=40), nullable=True),
        sa.Column("promised_days", sa.Integer(), nullable=True),
        sa.Column("tracking_number", sa.String(length=80), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "handed_over",
                "in_transit",
                "problem",
                "delivered",
                "returned",
                name="shipmentstatus",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('handed_over', 'in_transit', 'problem', 'delivered', 'returned')",
            name=op.f("ck_shipments_status_enum"),
        ),
        sa.CheckConstraint(
            "delivered_at IS NULL OR delivered_at >= shipped_at",
            name=op.f("ck_shipments_delivered_after_shipped"),
        ),
        sa.CheckConstraint(
            "promised_days IS NULL OR promised_days >= 0",
            name=op.f("ck_shipments_promise_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_shipments_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pack_task_id"],
            ["packaging_tasks.id"],
            name=op.f("fk_shipments_pack_task_id_packaging_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
    )
    op.create_index(
        "ix_shipments_carrier_shipped", "shipments", ["carrier_code", "shipped_at"], unique=False
    )
    op.create_index("ix_shipments_order_id", "shipments", ["order_id"], unique=False)
    op.create_index("ix_shipments_pack_task_id", "shipments", ["pack_task_id"], unique=False)
    op.create_index("ix_shipments_shipped_at", "shipments", ["shipped_at"], unique=False)
    op.create_index(
        "ix_shipments_status_shipped", "shipments", ["status", "shipped_at"], unique=False
    )

    op.create_table(
        "shipment_events",
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "handed_over",
                "scan",
                "delay",
                "damaged",
                "delivered",
                "returned",
                "note",
                name="eventkind",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "system", "person", "carrier", name="eventsource", native_enum=False, length=40
            ),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
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
            "kind IN ('handed_over', 'scan', 'delay', 'damaged', 'delivered', 'returned', 'note')",
            name=op.f("ck_shipment_events_kind_enum"),
        ),
        sa.CheckConstraint(
            "source IN ('system', 'person', 'carrier')",
            name=op.f("ck_shipment_events_source_enum"),
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            name=op.f("fk_shipment_events_recorded_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_shipment_events_shipment_id_shipments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_events")),
    )
    op.create_index(
        "ix_shipment_events_recorded_by", "shipment_events", ["recorded_by"], unique=False
    )
    op.create_index(
        "ix_shipment_events_shipment_at", "shipment_events", ["shipment_id", "at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_shipment_events_shipment_at", table_name="shipment_events")
    op.drop_index("ix_shipment_events_recorded_by", table_name="shipment_events")
    op.drop_table("shipment_events")
    for name in (
        "ix_shipments_status_shipped",
        "ix_shipments_shipped_at",
        "ix_shipments_pack_task_id",
        "ix_shipments_order_id",
        "ix_shipments_carrier_shipped",
    ):
        op.drop_index(name, table_name="shipments")
    op.drop_table("shipments")
