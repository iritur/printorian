"""schema hardening: jsonb, foreign keys, constraints, indexes, rate snapshots

Everything in this migration is a change that gets *more expensive with data*, which
is why it is one migration rather than a backlog. On a near-empty database it runs in
under a second; the same statements against a year of production are a maintenance
window, and the JSONB conversion in particular rewrites every affected table under an
``ACCESS EXCLUSIVE`` lock.

Grouped in five parts:

1. ``rate_snapshots`` — the table ADR-0002 always implied. ``orders`` stored the
   snapshot *hash*, but the rates behind it lived only in code, so changing a rate
   made every older hash unresolvable and "recompute this quote years later" was not
   actually possible.
2. **JSON → JSONB** on all thirteen JSON columns (ADR-0017).
3. **Foreign keys and types** for the four id columns that referenced real rows with
   nothing checking it, and the one — ``material_lots.printer_id`` — that was text
   while the identical relationship next to it was a UUID key.
4. **Constraints**: unique ``(parent, sequence)`` on the three append-only tables
   whose ordering column was documented as dependable and enforced nowhere, plus
   CHECK constraints on the money and mass invariants.
5. **Indexes**: the two foreign keys with no index at all (``job_events.job_id``,
   ``refunds.payment_id``), partial indexes on the two hot status predicates, and the
   removal of one redundant index.

Revision ID: 0005_schema_hardening
Revises: 21c394b4d2f0
Created: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_schema_hardening"
down_revision: str | None = "21c394b4d2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Every column that was ``json`` and should have been ``jsonb``.
JSON_COLUMNS: tuple[tuple[str, str], ...] = (
    ("orders", "price_breakdown"),
    ("order_lines", "colors"),
    ("order_lines", "finishes"),
    ("order_lines", "mesh"),
    ("order_events", "details"),
    ("payment_notifications", "payload"),
    ("printers", "last_telemetry"),
    ("service_operations", "materials_used"),
    ("prepared_plates", "filament_grams"),
    ("print_jobs", "colors"),
    ("job_events", "details"),
    ("assignment_records", "candidates"),
    ("wait_list_entries", "blocking_reasons"),
)

#: ``(table, constraint_name, expression)``. Names are bare; the metadata naming
#: convention prefixes them with ``ck_<table>_``.
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("orders", "total_non_negative", "total >= 0"),
    ("orders", "sla_credit_non_negative", "sla_credit >= 0"),
    ("orders", "sla_credit_within_total", "sla_credit <= total"),
    ("order_lines", "quantity_positive", "quantity > 0"),
    ("order_lines", "scale_positive", "scale > 0"),
    ("order_lines", "line_total_non_negative", "line_total >= 0"),
    ("order_lines", "estimated_minutes_non_negative", "estimated_minutes >= 0"),
    ("order_lines", "estimated_grams_non_negative", "estimated_grams >= 0"),
    ("order_events", "sequence_positive", "sequence >= 1"),
    ("payments", "amount_non_negative", "amount >= 0"),
    ("payments", "refunded_non_negative", "refunded_amount >= 0"),
    ("payments", "refunded_within_amount", "refunded_amount <= amount"),
    ("refunds", "amount_non_negative", "amount >= 0"),
    ("refunds", "sequence_positive", "sequence >= 1"),
    ("material_specs", "density_positive", "density_g_per_cm3 > 0"),
    ("material_specs", "sell_price_non_negative", "sell_price_per_gram >= 0"),
    (
        "material_specs",
        "purchase_price_non_negative",
        "purchase_price_per_1000m IS NULL OR purchase_price_per_1000m >= 0",
    ),
    ("material_lots", "initial_grams_non_negative", "initial_grams >= 0"),
    ("material_lots", "remaining_grams_non_negative", "remaining_grams >= 0"),
    ("material_lots", "remaining_within_initial", "remaining_grams <= initial_grams"),
    ("printers", "build_width_positive", "build_width_mm > 0"),
    ("printers", "build_depth_positive", "build_depth_mm > 0"),
    ("printers", "build_height_positive", "build_height_mm > 0"),
    ("printers", "nozzle_diameter_positive", "nozzle_diameter_mm > 0"),
    ("printers", "printed_hours_non_negative", "printed_hours >= 0"),
    ("printers", "lifetime_positive", "expected_lifetime_hours > 0"),
    ("printers", "power_non_negative", "nominal_power_kw >= 0"),
    ("printers", "acquisition_cost_non_negative", "acquisition_cost >= 0"),
    ("ams_slots", "unit_non_negative", "unit >= 0"),
    ("ams_slots", "index_non_negative", '"index" >= 0'),
    (
        "ams_slots",
        "remaining_percent_range",
        "remaining_percent IS NULL OR (remaining_percent BETWEEN 0 AND 100)",
    ),
    ("service_operations", "interval_positive", "interval_hours > 0"),
    ("service_operations", "last_done_hours_non_negative", "last_done_at_hours >= 0"),
    ("prepared_plates", "print_minutes_non_negative", "print_minutes >= 0"),
    ("prepared_plates", "scale_positive", "scale > 0"),
    ("prepared_plates", "size_non_negative", "size_bytes IS NULL OR size_bytes >= 0"),
    ("print_jobs", "attempt_positive", "attempt >= 1"),
    ("print_jobs", "grams_non_negative", "grams_required >= 0"),
    ("print_jobs", "estimated_minutes_non_negative", "estimated_minutes >= 0"),
    (
        "print_jobs",
        "progress_range",
        "progress_percent IS NULL OR (progress_percent BETWEEN 0 AND 100)",
    ),
    (
        "print_jobs",
        "finished_after_started",
        "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
    ),
    ("job_events", "sequence_non_negative", "sequence >= 0"),
    ("estimate_variances", "tolerance_non_negative", "tolerance >= 0"),
)

#: Statuses an order can still be worked in — the predicate of the partial index the
#: order desk reads through. Must stay in step with `ordering.policies.OPEN_STATUSES`;
#: spelled out here because a migration must not import application code that may be
#: renamed underneath it.
OPEN_ORDER_STATUSES = (
    "awaiting_payment",
    "draft",
    "packing",
    "paid",
    "post_production",
    "prep",
    "price_review",
    "printing",
    "quality_check",
    "queued",
    "shipped",
)


def upgrade() -> None:
    _create_rate_snapshots()
    _convert_json_to_jsonb()
    _repair_references()
    _add_constraints()
    _adjust_indexes()
    _create_order_number_sequence()


def downgrade() -> None:
    _drop_order_number_sequence()
    _restore_indexes()
    _drop_constraints()
    _restore_references()
    _convert_jsonb_to_json()
    _drop_rate_snapshots()


# --------------------------------------------------------------- 1. rates


def _create_rate_snapshots() -> None:
    op.create_table(
        "rate_snapshots",
        # The content hash *is* the key: identical rates are the same snapshot, so
        # two orders priced alike must collapse to one row rather than two.
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("engine_version", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_snapshots")),
    )

    # Orders written before this table existed carry '' — no snapshot rather than a
    # snapshot with an empty id. NULL says that honestly and lets the key be real.
    #
    # They also carry a *non-empty* hash whose rates lived only in code, which is
    # the whole reason this table is being added. Those ids reference nothing, so
    # the foreign key below cannot be created while they stand: on an empty
    # database this migration passes, and on every database with real orders in it
    # the ALTER fails. Both cases are cleared here, by the same rule.
    #
    # Nulled rather than back-filled from today's defaults. Inventing a snapshot
    # would claim the order was priced at rates it was not, which is worse than
    # admitting the rates are unrecoverable — and the column is nullable precisely
    # so it can say that.
    #
    # Nullable *first*: the column is NOT NULL until this runs, so clearing the
    # rows before widening it fails on the first row it would have fixed.
    op.alter_column("orders", "rate_snapshot_id", existing_type=sa.String(length=64), nullable=True)
    op.execute("""
        UPDATE orders SET rate_snapshot_id = NULL
        WHERE rate_snapshot_id = ''
           OR rate_snapshot_id NOT IN (SELECT id FROM rate_snapshots)
    """)
    # RESTRICT: a snapshot an order depends on must not be deletable while it does.
    op.create_foreign_key(
        op.f("fk_orders_rate_snapshot_id_rate_snapshots"),
        "orders",
        "rate_snapshots",
        ["rate_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _drop_rate_snapshots() -> None:
    op.drop_constraint(
        op.f("fk_orders_rate_snapshot_id_rate_snapshots"), "orders", type_="foreignkey"
    )
    op.execute("UPDATE orders SET rate_snapshot_id = '' WHERE rate_snapshot_id IS NULL")
    op.alter_column(
        "orders", "rate_snapshot_id", existing_type=sa.String(length=64), nullable=False
    )
    op.drop_table("rate_snapshots")


# ---------------------------------------------------------------- 2. jsonb


def _convert_json_to_jsonb() -> None:
    """Rewrite every JSON column as JSONB.

    The ``USING`` clause is required: PostgreSQL will not cast ``json`` to ``jsonb``
    implicitly, and without it the ``ALTER`` fails outright rather than doing
    something surprising.
    """
    for table, column in JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSON(),
            type_=postgresql.JSONB(),
            existing_nullable=False,
            postgresql_using=f"{column}::jsonb",
        )


def _convert_jsonb_to_json() -> None:
    for table, column in JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSONB(),
            type_=postgresql.JSON(),
            existing_nullable=False,
            postgresql_using=f"{column}::json",
        )


# ----------------------------------------------------------- 3. references


def _repair_references() -> None:
    # `material_lots.printer_id` was String(80) while `ams_slots.printer_id` beside
    # it was a UUID foreign key: two spellings of one relationship, one uncheckable.
    op.alter_column(
        "material_lots",
        "printer_id",
        existing_type=sa.String(length=80),
        type_=sa.Uuid(),
        existing_nullable=True,
        postgresql_using="NULLIF(printer_id, '')::uuid",
    )
    op.create_foreign_key(
        op.f("fk_material_lots_printer_id_printers"),
        "material_lots",
        "printers",
        ["printer_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # The third corner of the material ↔ slot ↔ printer triangle the scheduler's
    # eligibility filter runs on, and the only one nothing checked.
    op.execute(
        "UPDATE ams_slots SET lot_id = NULL "
        "WHERE lot_id IS NOT NULL "
        "AND lot_id NOT IN (SELECT id FROM material_lots)"
    )
    op.create_foreign_key(
        op.f("fk_ams_slots_lot_id_material_lots"),
        "ams_slots",
        "material_lots",
        ["lot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        "UPDATE prepared_plates SET sliced_by = NULL "
        "WHERE sliced_by IS NOT NULL AND sliced_by NOT IN (SELECT id FROM users)"
    )
    op.create_foreign_key(
        op.f("fk_prepared_plates_sliced_by_users"),
        "prepared_plates",
        "users",
        ["sliced_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        "UPDATE order_events SET actor_id = NULL "
        "WHERE actor_id IS NOT NULL AND actor_id NOT IN (SELECT id FROM users)"
    )
    op.create_foreign_key(
        op.f("fk_order_events_actor_id_users"),
        "order_events",
        "users",
        ["actor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Typed, but deliberately *not* a foreign key — see the column's docstring. An
    # audit record of one moment must survive the machine it names being retired.
    op.alter_column(
        "assignment_records",
        "chosen_printer_id",
        existing_type=sa.String(length=64),
        type_=sa.Uuid(),
        existing_nullable=True,
        postgresql_using="NULLIF(chosen_printer_id, '')::uuid",
    )

    op.alter_column(
        "prepared_plates",
        "size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def _restore_references() -> None:
    op.alter_column(
        "prepared_plates",
        "size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        "assignment_records",
        "chosen_printer_id",
        existing_type=sa.Uuid(),
        type_=sa.String(length=64),
        existing_nullable=True,
        postgresql_using="chosen_printer_id::text",
    )
    op.drop_constraint(op.f("fk_order_events_actor_id_users"), "order_events", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_prepared_plates_sliced_by_users"), "prepared_plates", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_ams_slots_lot_id_material_lots"), "ams_slots", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_material_lots_printer_id_printers"), "material_lots", type_="foreignkey"
    )
    op.alter_column(
        "material_lots",
        "printer_id",
        existing_type=sa.Uuid(),
        type_=sa.String(length=80),
        existing_nullable=True,
        postgresql_using="printer_id::text",
    )


# ---------------------------------------------------------- 4. constraints


def _add_constraints() -> None:
    # The append-only tables whose `sequence` column is documented as "the only
    # dependable ordering" and which nothing made dependable. Duplicates are
    # collapsed first, oldest row keeping the position, so the constraint can be
    # created on data that predates it.
    _renumber_duplicates("order_events", "order_id")
    op.create_unique_constraint(
        "uq_order_events_order_id_sequence", "order_events", ["order_id", "sequence"]
    )
    _renumber_duplicates("job_events", "job_id")
    op.create_unique_constraint(
        "uq_job_events_job_id_sequence", "job_events", ["job_id", "sequence"]
    )
    _renumber_duplicates("refunds", "payment_id")
    op.create_unique_constraint(
        "uq_refunds_payment_id_sequence", "refunds", ["payment_id", "sequence"]
    )

    # A physical slot cannot exist twice. Was a plain index, so a retried telemetry
    # write could double a slot and show the scheduler capacity that is not there.
    op.execute(
        """
        DELETE FROM ams_slots a USING ams_slots b
        WHERE a.printer_id = b.printer_id AND a.unit = b.unit AND a.index = b.index
          AND a.created_at > b.created_at
        """
    )
    op.drop_index("ix_ams_slots_printer_id_unit_index", table_name="ams_slots")
    op.create_unique_constraint(
        "uq_ams_slots_printer_id_unit_index", "ams_slots", ["printer_id", "unit", "index"]
    )

    for table, name, expression in CHECKS:
        op.create_check_constraint(name, table, expression)


def _drop_constraints() -> None:
    for table, name, _ in reversed(CHECKS):
        # `op.f` marks the name as final. Without it the metadata naming convention
        # runs again over an already-conventional name and looks for
        # `ck_<table>_ck_<table>_<name>`, which of course does not exist.
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")

    op.drop_constraint("uq_ams_slots_printer_id_unit_index", "ams_slots", type_="unique")
    op.create_index(
        "ix_ams_slots_printer_id_unit_index",
        "ams_slots",
        ["printer_id", "unit", "index"],
        unique=False,
    )
    op.drop_constraint("uq_refunds_payment_id_sequence", "refunds", type_="unique")
    op.drop_constraint("uq_job_events_job_id_sequence", "job_events", type_="unique")
    op.drop_constraint("uq_order_events_order_id_sequence", "order_events", type_="unique")


def _renumber_duplicates(table: str, parent: str) -> None:
    """Give every row a distinct position within its parent, oldest first.

    Only rewrites rows that actually collide. The ordering is by ``created_at`` then
    ``id``, which is the same order the application intended and the only one
    available after the fact.
    """
    op.execute(
        f"""
        WITH renumbered AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY {parent} ORDER BY sequence, created_at, id
            ) AS position
            FROM {table}
        )
        UPDATE {table} AS t
        SET sequence = r.position
        FROM renumbered r
        WHERE t.id = r.id AND t.sequence <> r.position
        """
    )


# -------------------------------------------------------------- 5. indexes


def _adjust_indexes() -> None:
    # Foreign keys with no index at all. PostgreSQL does not create one for you, so
    # every read through them — and every cascading delete — was a sequential scan.
    #
    # `job_events.job_id` is absent from this list on purpose: the new
    # `(job_id, sequence)` unique constraint builds an index led by `job_id`, which
    # serves the same lookups. A separate one would be pure write cost.
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"], unique=False)
    # Foreign keys whose delete rule rewrites this table — `SET NULL` on the
    # parent, or `RESTRICT` checked against it. Unindexed, each of those is a
    # sequential scan of the child at the moment somebody removes a user, a
    # printer or a plate.
    op.create_index("ix_orders_rate_snapshot_id", "orders", ["rate_snapshot_id"], unique=False)
    op.create_index("ix_order_events_actor_id", "order_events", ["actor_id"], unique=False)
    op.create_index("ix_prepared_plates_sliced_by", "prepared_plates", ["sliced_by"], unique=False)
    op.create_index("ix_ams_slots_lot_id", "ams_slots", ["lot_id"], unique=False)
    op.create_index(
        "ix_print_jobs_prepared_plate_id", "print_jobs", ["prepared_plate_id"], unique=False
    )
    op.create_index(
        "ix_payment_notifications_payment_id",
        "payment_notifications",
        ["payment_id"],
        unique=False,
    )
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"], unique=False)
    op.create_index(
        "ix_service_operations_printer_id", "service_operations", ["printer_id"], unique=False
    )
    op.create_index("ix_material_lots_printer_id", "material_lots", ["printer_id"], unique=False)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)
    op.create_index(
        "ix_estimate_variances_order_id", "estimate_variances", ["order_id"], unique=False
    )
    op.create_index(
        "ix_wait_list_entries_order_id", "wait_list_entries", ["order_id"], unique=False
    )
    op.create_index(
        "ix_wait_list_entries_predicted_start",
        "wait_list_entries",
        ["predicted_start"],
        unique=False,
    )
    op.create_index(
        "ix_assignment_records_created_at",
        "assignment_records",
        [sa.text("created_at DESC")],
        unique=False,
    )

    # Partial indexes on the two hot status predicates. Both stay roughly the size of
    # the *live* work rather than of all history, which is what keeps them cached.
    statuses = ", ".join(f"'{status}'" for status in OPEN_ORDER_STATUSES)
    op.create_index(
        "ix_orders_open_created_at",
        "orders",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text(f"status IN ({statuses})"),
    )
    op.create_index(
        "ix_print_jobs_ready_priority",
        "print_jobs",
        ["priority", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'ready'"),
    )

    # `order_events` is read ordered by `sequence`; the old index sorted by
    # `created_at`, which nothing sorts on. The new unique constraint covers it.
    op.drop_index("ix_order_events_order_id_created_at", table_name="order_events")
    # `plate_key` already has a unique constraint, which builds its own index. The
    # second one cost a write per insert and served no read the first could not.
    op.drop_index("ix_prepared_plates_plate_key", table_name="prepared_plates")


def _restore_indexes() -> None:
    op.create_index("ix_prepared_plates_plate_key", "prepared_plates", ["plate_key"], unique=False)
    op.create_index(
        "ix_order_events_order_id_created_at",
        "order_events",
        ["order_id", "created_at"],
        unique=False,
    )
    for name, table in (
        ("ix_print_jobs_ready_priority", "print_jobs"),
        ("ix_orders_open_created_at", "orders"),
        ("ix_assignment_records_created_at", "assignment_records"),
        ("ix_wait_list_entries_predicted_start", "wait_list_entries"),
        ("ix_wait_list_entries_order_id", "wait_list_entries"),
        ("ix_estimate_variances_order_id", "estimate_variances"),
        ("ix_sessions_expires_at", "sessions"),
        ("ix_material_lots_printer_id", "material_lots"),
        ("ix_service_operations_printer_id", "service_operations"),
        ("ix_order_lines_order_id", "order_lines"),
        ("ix_payment_notifications_payment_id", "payment_notifications"),
        ("ix_print_jobs_prepared_plate_id", "print_jobs"),
        ("ix_ams_slots_lot_id", "ams_slots"),
        ("ix_prepared_plates_sliced_by", "prepared_plates"),
        ("ix_order_events_actor_id", "order_events"),
        ("ix_orders_rate_snapshot_id", "orders"),
        ("ix_refunds_payment_id", "refunds"),
    ):
        op.drop_index(name, table_name=table)


# ------------------------------------------------------------- 6. sequence


def _create_order_number_sequence() -> None:
    """Order numbers stop being ``SELECT count(*) FROM orders``.

    Started past the highest number already issued, so the sequence never hands out
    one that exists — the unique constraint would otherwise reject it.
    """
    op.execute("CREATE SEQUENCE IF NOT EXISTS order_number_seq START WITH 1")
    op.execute(
        """
        SELECT setval(
            'order_number_seq',
            GREATEST((SELECT count(*) FROM orders), 1),
            (SELECT count(*) FROM orders) > 0
        )
        """
    )


def _drop_order_number_sequence() -> None:
    op.execute("DROP SEQUENCE IF EXISTS order_number_seq")
