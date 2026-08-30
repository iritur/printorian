"""the order pins the decay terms it was sold under, not just their name

``orders.decay_policy`` recorded which rule a promise was sold under and nothing
about what that rule *said*. The numbers lived in ``POLICIES`` in
``ordering/policies.py``, and ``_credit_for`` re-read them on every sweep — so
raising ``standard`` from 5%/day to 10%/day did not apply to new orders. It
re-priced every promise not yet shipped, at the new rate, on the next pass of
``workers/sla.py``. This is ADR-0020's trap on the other money path.

**The three known policies are backfilled with the values they hold today, and
that is not an invented number.** Those values are what every existing order is
being priced at right now, so writing them down changes nothing about what any
customer is owed — it only stops the next edit from reaching backwards. The
alternative, leaving history null, would keep exactly the orders the fix is for
exposed to the defect.

Rows carrying any other code are deliberately left null. Their terms were never
recorded anywhere, and there is nothing to recover them from; ``_terms_for``
falls back to the live lookup for those, which is what the code did before this
revision. A guess would be worse than the gap (ADR-0007).

The values are spelled out rather than imported from ``POLICIES``, for the reason
0019 gives at length: a migration has to keep meaning what it meant on the day it
ran, and an import would let a rate edit years from now rewrite history.

Revision ID: 0020_order_decay_terms
Revises: 0019_enum_check_constraints
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision: str = "0020_order_decay_terms"
down_revision: str | None = "0019_enum_check_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: ``code -> (percent per day, grace in seconds, cap)`` as ``POLICIES`` held them
#: on the day this revision was written.
_TERMS: dict[str, tuple[Decimal, int, Decimal]] = {
    "standard": (Decimal(5), 43200, Decimal(30)),
    "none": (Decimal(0), 43200, Decimal(0)),
    "strict": (Decimal(10), 7200, Decimal(50)),
}

#: ``(name, expression)``. The metadata naming convention turns each name into
#: ``ck_orders_<name>``, so these have to match `Order.__table_args__` exactly or
#: ``alembic check`` reports drift.
_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("decay_percent_per_day_non_negative", "decay_percent_per_day >= 0"),
    ("decay_grace_seconds_non_negative", "decay_grace_seconds >= 0"),
    (
        "decay_max_percent_within_range",
        "decay_max_percent >= 0 AND decay_max_percent <= 100",
    ),
    (
        "decay_terms_all_or_none",
        "num_nonnulls(decay_percent_per_day, decay_grace_seconds, decay_max_percent) IN (0, 3)",
    ),
)


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("decay_percent_per_day", sa.Numeric(precision=6, scale=2), nullable=True),
    )
    op.add_column("orders", sa.Column("decay_grace_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("decay_max_percent", sa.Numeric(precision=5, scale=2), nullable=True),
    )

    # Every parameter carries its column's type. Without that, asyncpg types a
    # bound `Decimal` argument by inference and PostgreSQL refuses to assign the
    # result to a numeric column — the failure is `DatatypeMismatchError` at
    # migration time, on the farm rather than here.
    statement = sa.text(
        "UPDATE orders SET decay_percent_per_day = :percent_per_day, "
        "decay_grace_seconds = :grace_seconds, decay_max_percent = :max_percent "
        "WHERE decay_policy = :code"
    )
    for code, (percent_per_day, grace_seconds, max_percent) in _TERMS.items():
        op.execute(
            statement.bindparams(
                sa.bindparam("percent_per_day", percent_per_day, type_=sa.Numeric(6, 2)),
                sa.bindparam("grace_seconds", grace_seconds, type_=sa.Integer()),
                sa.bindparam("max_percent", max_percent, type_=sa.Numeric(5, 2)),
                sa.bindparam("code", code, type_=sa.String(32)),
            )
        )

    # After the backfill, so a row written by an older application version cannot
    # fail the all-or-none rule before it has been filled in.
    for name, expression in _CONSTRAINTS:
        op.create_check_constraint(name, "orders", expression)


def downgrade() -> None:
    # The bare name, not the rendered one: `drop_constraint` runs the naming
    # convention over what it is given, so passing `ck_orders_…` asks PostgreSQL
    # for `ck_orders_ck_orders_…` and the downgrade dies half-applied.
    for name, _expression in reversed(_CONSTRAINTS):
        op.drop_constraint(name, "orders", type_="check")

    op.drop_column("orders", "decay_max_percent")
    op.drop_column("orders", "decay_grace_seconds")
    op.drop_column("orders", "decay_percent_per_day")
