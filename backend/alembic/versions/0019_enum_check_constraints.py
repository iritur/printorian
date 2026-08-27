"""enum columns get the database-level CHECK they had been doing without

Every enum column in the schema was a bare ``VARCHAR``. The Python type validated
on write and Pydantic validated at the edge, so nothing the *application* wrote
could be wrong — but a psql session, a data-fix script or an import job reaches
these columns with neither, and PostgreSQL would have taken whatever it was given.

The reason it stayed that way was a naming collision, not a judgement about value:
SQLAlchemy names a generated enum constraint after the enum *type*, so
``order_events`` — whose ``from_status`` and ``to_status`` are both ``OrderStatus``
— produced two constraints called ``ck_order_events_orderstatus`` and the schema
would not build. ``core.db._CheckedEnum`` now derives the name from the column
instead, which is unique within a table by definition, so the collision cannot
happen for the twenty-fourth enum column either.

The values are spelled out below rather than imported from the enums. A migration
must keep meaning what it meant on the day it ran, and an import would let a rename
years from now rewrite history; 0005 spells out ``OPEN_ORDER_STATUSES`` for the
same reason. The consequence is real and intended: **adding a member to an enum now
needs a migration** that rewrites the constraint. That is the same review the new
value would otherwise never get.

Nullable columns are covered too — ``x IN (...)`` is NULL, not false, for a NULL
``x``, so NULL still passes and the column keeps meaning "not set".

If this migration fails on ``ALTER TABLE``, it has found a row whose value is
outside its enum. Do not widen the constraint to admit it: that row is the thing
issue #43 named as the trigger for building this, and it wants reading before it
wants keeping.

**What this costs on a farm with real history.** Each ``ADD CONSTRAINT`` takes
``ACCESS EXCLUSIVE`` on its table and scans every row — imperceptible on the tables
here today, and a stall on the telemetry writer if ``telemetry_samples`` has grown
into the hundreds of millions ADR-0018 plans for. The two-step ``NOT VALID`` then
``VALIDATE`` form that DEVELOPMENT.md describes does not help *inside* a migration:
Alembic runs this in one transaction, so the strong lock is held until it commits
either way. A farm large enough to care should run the statements by hand, one
autocommitted pair per table, and stamp the revision afterwards.

Revision ID: 0019_enum_check_constraints
Revises: 0018_settings
Created: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_enum_check_constraints"
down_revision: str | None = "0018_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: ``(table, column, permitted values)`` — every enum column in the schema as of this
#: revision. The constraint name is ``ck_<table>_<column>_enum``, which is what the
#: metadata naming convention produces from ``core.db._CheckedEnum``. `alembic check`
#: will not police the contents of these: it matches CHECK constraints by name and
#: stops there. `test_every_enum_column_is_checked_in_the_database` compares the value
#: sets against the migrated database, and is what catches an enum changed without a
#: migration to match.
ENUM_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("catalog_model_materials", "suitability", ("excellent", "good", "limited")),
    ("catalog_models", "category", ("func", "case", "mech", "org", "decor")),
    ("catalog_models", "size_class", ("s", "m", "l")),
    (
        "journal_posts",
        "section",
        ("cost", "materials", "fleet", "architecture", "postprocessing"),
    ),
    ("material_lots", "location_kind", ("stock", "printer", "dryer", "consumed")),
    ("model_assets", "format", ("stl", "3mf", "other")),
    (
        "order_events",
        "from_status",
        (
            "draft",
            "awaiting_payment",
            "paid",
            "prep",
            "price_review",
            "queued",
            "printing",
            "post_production",
            "quality_check",
            "packing",
            "shipped",
            "completed",
            "cancelled",
            "refunded",
        ),
    ),
    (
        "order_events",
        "to_status",
        (
            "draft",
            "awaiting_payment",
            "paid",
            "prep",
            "price_review",
            "queued",
            "printing",
            "post_production",
            "quality_check",
            "packing",
            "shipped",
            "completed",
            "cancelled",
            "refunded",
        ),
    ),
    (
        "orders",
        "status",
        (
            "draft",
            "awaiting_payment",
            "paid",
            "prep",
            "price_review",
            "queued",
            "printing",
            "post_production",
            "quality_check",
            "packing",
            "shipped",
            "completed",
            "cancelled",
            "refunded",
        ),
    ),
    ("packaging_tara", "kind", ("bag", "box", "wrap", "filler")),
    (
        "packaging_tasks",
        "hold_reason",
        ("invoice_unpaid", "waybill_missing", "address_incomplete", "item_missing"),
    ),
    (
        "packaging_tasks",
        "status",
        ("checked", "packing", "held", "ready", "shipped", "cancelled"),
    ),
    (
        "payments",
        "status",
        (
            "created",
            "pending",
            "succeeded",
            "partially_refunded",
            "refunded",
            "cancelled",
            "failed",
        ),
    ),
    (
        "postproduction_operations",
        "kind",
        ("support_removal", "sanding", "priming", "painting", "polishing", "assembly"),
    ),
    (
        "postproduction_tasks",
        "status",
        (
            "waiting",
            "in_progress",
            "paused",
            "curing",
            "for_qc",
            "returned",
            "done",
            "cancelled",
        ),
    ),
    ("prepared_plates", "status", ("valid", "stale", "rejected")),
    (
        "print_jobs",
        "status",
        (
            "pending",
            "on_hold",
            "ready",
            "assigned",
            "dispatching",
            "printing",
            "succeeded",
            "failed",
            "cancelled",
        ),
    ),
    ("printers", "connection_mode", ("lan", "cloud", "manual", "mock")),
    (
        "printers",
        "state",
        (
            "offline",
            "idle",
            "preparing",
            "printing",
            "paused",
            "finished",
            "error",
            "maintenance",
        ),
    ),
    (
        "service_operations",
        "kind",
        (
            "nozzle_change",
            "belt_tension",
            "lubrication",
            "bed_level",
            "filter_change",
            "deep_clean",
        ),
    ),
    # Declaratively partitioned. A CHECK added to the parent recurses into every
    # existing partition and is inherited by every future one, so `fleet.retention`
    # keeps creating months that carry it without knowing this ran.
    (
        "telemetry_samples",
        "state",
        (
            "offline",
            "idle",
            "preparing",
            "printing",
            "paused",
            "finished",
            "error",
            "maintenance",
        ),
    ),
    ("users", "customer_kind", ("person", "company")),
    ("users", "role", ("customer", "operator", "engineer", "manager", "owner")),
)


def _predicate(column: str, values: tuple[str, ...]) -> str:
    """Render ``col IN ('a', 'b')`` the way SQLAlchemy's own enum CHECK renders it.

    Matching the generated form matters: a database built from these migrations and
    one built from the ORM metadata must be comparable by eye, not only by name.
    """
    literals = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({literals})"


def upgrade() -> None:
    # The name is bare here; the metadata naming convention prefixes `ck_<table>_`,
    # which is exactly what `_CheckedEnum` produces for the same column.
    for table, column, values in ENUM_COLUMNS:
        op.create_check_constraint(f"{column}_enum", table, _predicate(column, values))


def downgrade() -> None:
    for table, column, _values in reversed(ENUM_COLUMNS):
        # `op.f` marks the name as final. Without it the naming convention runs again
        # over an already-conventional name and looks for `ck_<table>_ck_<table>_...`,
        # which of course does not exist.
        op.drop_constraint(op.f(f"ck_{table}_{column}_enum"), table, type_="check")
