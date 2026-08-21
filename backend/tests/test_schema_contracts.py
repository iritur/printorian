"""Schema rules that CI enforces, in the style of the import contracts.

Mess is not prevented by intention; it is prevented by CI failing
(ARCHITECTURE §11). These are the database's version of that, and the first one
exists because five columns named ``*_id`` pointed at real rows with nothing
checking it — including all three corners of the material ↔ slot ↔ printer
triangle the scheduler's hard eligibility filter runs on.

Pure metadata inspection: no database, no fixtures, fast enough to run on every
commit.
"""

from __future__ import annotations

import pytest
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

import printorian.models  # noqa: F401 - registers every table on the metadata
from printorian.core.db import Base

#: Columns named ``*_id`` that deliberately have no foreign key, each with the
#: reason. Adding to this list is the point: it forces the choice to be made and
#: written down rather than made by omission.
UNLINKED_IDS: dict[str, str] = {
    "assignment_records.chosen_printer_id": (
        "An immutable audit record of one moment. Both delete rules are wrong: "
        "SET NULL erases the answer to the only question the table exists to "
        "answer, and RESTRICT makes retiring a printer impossible while any "
        "decision mentions it. `candidates` stores printer ids loose for the "
        "same reason."
    ),
    "telemetry_samples.printer_id": (
        "High-volume partitioned history, dropped a partition at a time. A "
        "cascading delete through hundreds of millions of rows is an outage "
        "rather than a cleanup, and decommissioning a machine must not rewrite "
        "what it was measured doing (ADR-0018)."
    ),
    "metric_rollups.printer_id": (
        "The same call as `telemetry_samples.printer_id`, one grain coarser. This "
        "table is what survives when the raw samples are dropped, so retiring a "
        "machine must not rewrite or delete it: SET NULL erases the only question "
        "the row answers — which machine spent that hour printing — and RESTRICT "
        "makes retirement impossible for as long as any hour of history remains, "
        "which is for ever, because nothing ever drops from here (ADR-0018)."
    ),
    "payments.provider_payment_id": (
        "The *gateway's* id for the payment, not a reference to anything here. "
        "Indexed for reconciliation lookups; there is no table to point at."
    ),
    "refunds.provider_refund_id": ("The gateway's id for the refund, for the same reason."),
}


def _identifier(table: str, column: str) -> str:
    return f"{table}.{column}"


def _id_columns() -> list[tuple[str, str]]:
    return [
        (table.name, column.name)
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name.endswith("_id") and column.name != "id"
    ]


@pytest.mark.parametrize(
    ("table", "column"),
    _id_columns(),
    ids=[_identifier(table, column) for table, column in _id_columns()],
)
def test_an_id_column_has_a_foreign_key_or_a_stated_reason(table: str, column: str) -> None:
    """Every ``*_id`` references something, or says in `UNLINKED_IDS` why it does not.

    This is the check that would have caught `material_lots.printer_id` being a
    ``String(80)`` while `ams_slots.printer_id` beside it was a UUID foreign key —
    two spellings of one relationship, one of which the database could not verify.
    """
    target = Base.metadata.tables[table].columns[column]
    if target.foreign_keys:
        return

    reason = UNLINKED_IDS.get(_identifier(table, column))
    assert reason, (
        f"{table}.{column} looks like a reference but has no foreign key. "
        f"Add one, or add an entry to UNLINKED_IDS in {__file__} saying why not."
    )


def test_every_sequence_column_is_unique_within_its_parent() -> None:
    """An append-only table's ``sequence`` is only dependable if the database says so.

    `OrderEvent.sequence` is documented as "the only dependable ordering" and
    nothing made it dependable: two writers both counted the existing rows, both
    got 7, and the history became silently ambiguous.
    """
    offenders = []
    for table in Base.metadata.sorted_tables:
        if "sequence" not in table.columns:
            continue
        covered = any(
            "sequence" in constraint.columns
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ) or any(index.unique and "sequence" in index.columns for index in table.indexes)
        if not covered:
            offenders.append(table.name)

    assert not offenders, (
        f"tables with an unconstrained `sequence`: {offenders}. "
        "Add a unique constraint on (parent_id, sequence) — without one the column "
        "is a suggestion, not an ordering."
    )


def test_every_foreign_key_is_indexed() -> None:
    """PostgreSQL does not index a foreign key for you.

    Without an index, reading through the key is a sequential scan and so is every
    cascading delete. `job_events.job_id` and `refunds.payment_id` had none at all,
    on tables that grow with every job and every refund forever.

    A composite index or unique constraint *led by* the column counts: a B-tree on
    ``(job_id, sequence)`` serves a lookup by ``job_id`` perfectly well, and a
    second index on ``job_id`` alone would be write cost for nothing.

    A composite **primary key** led by the column counts for the same reason —
    PostgreSQL backs one with a unique B-tree exactly as it does a UNIQUE
    constraint, so ``PRIMARY KEY (model_id, material_code)`` already serves
    ``WHERE model_id = ?``. Only the *leading* column is credited, so a table whose
    key is ``(id)`` still cannot excuse an unindexed ``owner_id``.
    """
    offenders = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not column.foreign_keys:
                continue
            leading = {
                next(iter(columns)).name
                for columns in (
                    *(index.columns for index in table.indexes),
                    *(
                        constraint.columns
                        for constraint in table.constraints
                        if isinstance(constraint, UniqueConstraint | PrimaryKeyConstraint)
                    ),
                )
                if len(columns) > 0
            }
            if column.name not in leading:
                offenders.append(f"{table.name}.{column.name}")

    assert not offenders, (
        f"foreign keys with no index leading on them: {offenders}. "
        "Every read through them, and every cascading delete, is a sequential scan."
    )


def test_no_column_uses_the_plain_json_type() -> None:
    """ADR-0017: JSON columns are `core.db.JsonB`, which is JSONB on PostgreSQL.

    A bare ``JSON()`` reads identically in Python and is a different type on disk —
    text, reparsed on every access, unindexable — and converting it later rewrites
    the whole table under an ``ACCESS EXCLUSIVE`` lock.
    """
    from sqlalchemy import JSON
    from sqlalchemy.dialects import postgresql

    offenders = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not isinstance(column.type, JSON):
                continue
            variant = column.type.dialect_impl(postgresql.dialect())
            if not isinstance(variant, postgresql.JSONB):
                offenders.append(f"{table.name}.{column.name}")

    assert not offenders, (
        f"columns using plain JSON instead of `core.db.JsonB`: {offenders}. See ADR-0017."
    )


def test_the_unlinked_id_list_has_no_stale_entries() -> None:
    """An exemption that no longer applies is a rule nobody is following."""
    known = {_identifier(table, column) for table, column in _id_columns()}
    stale = {
        entry
        for entry in UNLINKED_IDS
        if not entry.startswith("*.")
        and (
            entry not in known
            or Base.metadata.tables[entry.split(".")[0]].columns[entry.split(".")[1]].foreign_keys
        )
    }
    assert not stale, f"UNLINKED_IDS entries that are no longer exemptions: {stale}"
