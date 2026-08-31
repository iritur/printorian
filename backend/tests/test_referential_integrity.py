"""Which delete rule each foreign key carries, and whether the database has it.

`test_schema_contracts.py` asserts that every ``*_id`` column *has* a foreign key.
That is a weaker claim than the one `docs/DATABASE-REVIEW.md` §3 actually makes,
which is that each of those keys carries a **deliberate** delete rule.
``order_lines.model_asset_id`` is ``RESTRICT``, and that rule is the entire reason
the retention sweep in `catalog.assets` never has to ask `ordering` anything: the
database refuses to collect geometry an open order still has to print. Nothing was
checking it. A rule flipped to ``CASCADE`` in a model is one word, would have passed
all six gates and the whole suite, and the first evidence of it would have been a
retention sweep deleting an order's lines.

The inventory below is all forty-eight keys rather than the interesting ones,
because a spot check leaves the other forty unwatched and because the forty-ninth
key should not be addable without somebody deciding what it does on delete. Rules
are grouped by *rule* rather than listed per table: the reason for a rule is shared,
and repeating it forty-eight times would be forty-eight places to keep in step.
Where an individual key is load-bearing, or reads wrong beside its neighbour, it is
called out under its group.

Two comparisons follow from it, and they are not redundant. The **metadata** must
agree with the inventory, which is a millisecond and catches the edit. The
**database** must agree too, read back out of `pg_constraint` — and that is a
different claim, because a declaration in a Python object is not a constraint until
PostgreSQL has been handed the DDL and has kept it. `core.db._CheckedEnum` exists
because SQLAlchemy quietly dropped enum CHECKs on exactly that gap (issue #43); an
assertion that only reads the metadata could not have seen it. Whether the
*migrations* agree with the models is a third question, and `test_migrations.py`
answers it with ``alembic check`` against a database Alembic built.

This file is also the tripwire under the fast suite's own premise. If a key stopped
being *present* on the schema `conftest.clean_database` builds — a metadata gone
short, a partial ``create_all`` — `test_the_test_database_carries_every_declared_key`
fails by name, rather than the suite quietly going back to asserting about a world
production cannot reach. That state is not hypothetical: it is what the suite was in
before ADR-0021, and it is why `tests/factories.py` exists (issue #47).

What the rules *do* is a separate question and a separate file:
`tests/unit/test_delete_rules.py` exercises one representative of each category
against real rows, because a catalogue is not behaviour (CLAUDE.md §2). The split is
load-bearing rather than tidy, and this is the case that shows it: a session that
runs ``SET session_replication_role = replica`` leaves every constraint in
`pg_constraint` and enforces none of them, so **every test in this file still
passes** and all six of the behaviour tests fail. Measured, not assumed — the
mutation was run. A present constraint and an enforced one are different facts, and
it takes both files to hold them.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import printorian.models  # noqa: F401 - registers every table on the metadata
from printorian.core.db import Base

#: Deleting the parent deletes the child, because the child has no meaning without
#: it: an order's lines and events, a job's history, a payment's refunds, a task's
#: steps. Each of these describes something *about* its parent and cannot be read
#: on its own, so leaving it behind would produce a row nobody can interpret and
#: nobody will ever delete.
CASCADE: frozenset[str] = frozenset(
    {
        "addresses.user_id",
        "ams_slots.printer_id",
        "assignment_records.job_id",
        "catalog_model_materials.model_id",
        "estimate_variances.job_id",
        "estimate_variances.order_id",
        "job_events.job_id",
        "material_lots.spec_id",
        "notification_prefs.user_id",
        "order_events.order_id",
        "order_lines.order_id",
        "packaging_instruction_steps.instruction_id",
        "packaging_task_steps.task_id",
        "packaging_task_tara.task_id",
        "packaging_tasks.order_id",
        "payment_notifications.payment_id",
        "postproduction_instruction_steps.operation_id",
        "postproduction_task_steps.task_id",
        "postproduction_tasks.order_id",
        "print_jobs.order_id",
        "refunds.payment_id",
        "service_operations.printer_id",
        "sessions.user_id",
        "sla_credit_entries.order_id",
        "wait_list_entries.job_id",
        "wait_list_entries.order_id",
    }
)

#: The child outlives the reference and keeps its own record. Removing a member of
#: staff must not delete the record of what they did; decommissioning a printer
#: must not destroy the jobs it ran or the lots that were loaded into it. The
#: reference goes, the row stays, and the column reads as "not recorded any more"
#: rather than as a machine that never existed.
#:
#: ``prepared_plates.model_asset_id`` sits here while ``print_jobs.model_asset_id``
#: two entries down is ``RESTRICT``, and the difference is the point: a plate is a
#: *cached* slice that can be produced again from the geometry, so losing the link
#: costs a re-slice. A job has to print that geometry, and there is nothing to fall
#: back to.
SET_NULL: frozenset[str] = frozenset(
    {
        "ams_slots.lot_id",
        "material_lots.printer_id",
        "model_assets.uploaded_by",
        "order_events.actor_id",
        "orders.customer_id",
        "packaging_tasks.operator_id",
        "packaging_tasks.tara_id",
        "postproduction_tasks.operator_id",
        "postproduction_tasks.printer_id",
        "prepared_plates.model_asset_id",
        "prepared_plates.sliced_by",
        "print_jobs.prepared_plate_id",
        "print_jobs.printer_id",
        "settings.updated_by",
        "settings_audit.changed_by",
    }
)

#: The parent may not be deleted at all while a child points at it. These are the
#: keys where losing the parent would leave work that cannot be completed or money
#: that cannot be explained: geometry an order or a job still has to print, the
#: rates a pinned price was computed from (ADR-0002 promises that price can be
#: recomputed years later), an order with a payment against it, the tare a
#: packaging task was measured into, the instruction a postproduction task is
#: following.
#:
#: ``order_lines.model_asset_id`` is the one to preserve above all. It is the whole
#: of what stops `catalog.assets`' retention sweep collecting a mesh an open order
#: depends on — which is why that sweep does not, and must not, consult `ordering`.
RESTRICT: frozenset[str] = frozenset(
    {
        "catalog_models.model_asset_id",
        "order_lines.model_asset_id",
        "orders.rate_snapshot_id",
        "packaging_task_tara.tara_id",
        "payments.order_id",
        "postproduction_tasks.operation_id",
        "print_jobs.model_asset_id",
    }
)

#: ``pg_constraint.confdeltype``, spelled the way the models spell it.
_CONFDELTYPE = {
    "a": "NO ACTION",
    "c": "CASCADE",
    "d": "SET DEFAULT",
    "n": "SET NULL",
    "r": "RESTRICT",
}

DECLARED: dict[str, str] = {
    **dict.fromkeys(CASCADE, "CASCADE"),
    **dict.fromkeys(SET_NULL, "SET NULL"),
    **dict.fromkeys(RESTRICT, "RESTRICT"),
}


def _declared_keys() -> dict[str, str]:
    """Every foreign key on the metadata, as ``table.column`` → ``ondelete``."""
    return {
        f"{table.name}.{column.name}": key.ondelete or "NO ACTION"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        for key in column.foreign_keys
    }


def test_every_foreign_key_has_a_rule_that_somebody_chose() -> None:
    """A new key must be classified above before it can be merged.

    The default in SQL is ``NO ACTION``, which is a rule nobody picked and which
    fails at the worst moment — a delete that has already been decided on, refused
    by a constraint the author of the reference never thought about. Requiring the
    entry is how the choice gets made on the commit that adds the key rather than
    during the incident that trips it.
    """
    actual = _declared_keys()
    unclassified = sorted(set(actual) - set(DECLARED))
    stale = sorted(set(DECLARED) - set(actual))

    assert not unclassified, (
        f"foreign keys with no delete rule declared in {__file__}: {unclassified}. "
        "Decide what happens to the child when the parent is deleted, and add it to "
        "CASCADE, SET_NULL or RESTRICT with the reason."
    )
    assert not stale, f"declared delete rules for foreign keys that no longer exist: {stale}"


def test_the_declared_rule_is_the_rule_the_model_asks_for() -> None:
    """The inventory above and the models must agree.

    Cheap, and it is the half that catches a rule *edited* rather than a key added:
    changing ``ondelete="RESTRICT"`` to ``"CASCADE"`` in a model is one word, passes
    every gate, and is the difference between refusing to delete geometry an order
    needs and deleting the order's lines along with it.
    """
    mismatched = {
        name: (rule, DECLARED[name])
        for name, rule in _declared_keys().items()
        if name in DECLARED and rule != DECLARED[name]
    }
    assert not mismatched, (
        f"models and the inventory in {__file__} disagree "
        f"(model, declared): {mismatched}. If the change is intended, change both — "
        "and write the migration, which `test_migrations.py` will ask for."
    )


async def _database_keys(session: AsyncSession) -> dict[str, str]:
    """Every foreign key PostgreSQL is actually holding, with its delete action.

    Read from the catalogue rather than from the metadata on purpose: this is the
    only assertion in the file that can tell the difference between what the models
    declare and what the database `conftest.clean_database` built is enforcing.

    Single-column keys only. Every key in this schema is one column, and the query
    reads ``conkey[1]``; a composite key would otherwise be reported under its first
    column alone and silently look correct. The width is asserted below rather than
    assumed, so the day one is added this says so instead of quietly under-reading.

    ``confdeltype`` is cast to ``text`` in the query because it is a ``"char"``,
    which asyncpg hands back as ``bytes`` — and a dictionary lookup on ``b'c'``
    raises `KeyError` a long way from anything that explains it.
    """
    rows = await session.execute(
        text(
            """
            SELECT child.relname AS child_table,
                   att.attname AS child_column,
                   con.confdeltype::text AS delete_action,
                   array_length(con.conkey, 1) AS columns
            FROM pg_constraint con
            JOIN pg_class child ON child.oid = con.conrelid
            JOIN pg_attribute att
              ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
            WHERE con.contype = 'f'
              AND child.relnamespace = 'public'::regnamespace
            """
        )
    )
    keys: dict[str, str] = {}
    for table, column, action, width in rows:
        assert width == 1, (
            f"{table}.{column} is part of a {width}-column foreign key, and this "
            "query only reads the first column of one. Widen it before trusting it."
        )
        keys[f"{table}.{column}"] = _CONFDELTYPE[action]
    return keys


async def test_the_test_database_carries_every_declared_key(db_session: AsyncSession) -> None:
    """Every declared key is present on the schema the fast suite runs against.

    The suite ran on SQLite until ADR-0021, which enforces no foreign key at all,
    and dozens of tests were built against parent ids that referenced nothing —
    asserting behaviour the real database would have refused outright.
    `tests/factories.py` exists to give those tests real parents, and it is only
    worth anything while the constraints are actually there. A short metadata or a
    partial ``create_all`` puts the suite back where it was, green and meaningless,
    and would show up in no other test.

    **Present is not the same as enforced**, and this assertion only covers the
    first: a session with ``session_replication_role = replica`` keeps every row in
    `pg_constraint` and checks none of them. That case is caught in
    `tests/unit/test_delete_rules.py`, which is why the behaviour tests are not
    optional company for this one.
    """
    missing = sorted(set(DECLARED) - set(await _database_keys(db_session)))
    assert not missing, (
        f"declared foreign keys the test database does not carry: {missing}. "
        "The fast suite is no longer running under the constraints it asserts about "
        "(ADR-0021)."
    )


async def test_the_database_holds_the_delete_rule_that_was_declared(
    db_session: AsyncSession,
) -> None:
    """The rule PostgreSQL is holding, not the rule the model file asks for.

    This is the assertion a deliberately wrong cascade fails, and it reads the
    catalogue rather than the metadata because a declaration is not a constraint
    until the DDL has been emitted and kept. That gap is not theoretical here:
    SQLAlchemy dropped every enum CHECK through it until `core.db._CheckedEnum`
    (issue #43), and no amount of inspecting the models would have shown it.
    """
    actual = await _database_keys(db_session)
    # ``get`` with a default rather than ``actual[name]``: a key the database does
    # not have at all belongs in this report as "absent", and indexing would turn
    # the finding into a `KeyError` naming an arbitrary neighbour instead. Measured
    # — the first version did exactly that when a foreign key was removed.
    wrong = {
        name: (actual.get(name, "absent"), rule)
        for name, rule in DECLARED.items()
        if actual.get(name) != rule
    }
    assert not wrong, (
        f"foreign keys whose delete rule is not what was declared (found, declared): {wrong}"
    )


def test_a_set_null_key_is_on_a_column_that_can_hold_null() -> None:
    """``SET NULL`` onto a ``NOT NULL`` column is a delete that can never succeed.

    It is a plausible mistake — the intent reads correctly at the reference — and
    the failure arrives far away, as a ``NotNullViolation`` during a delete that had
    nothing obviously to do with the column. Deleting a printer would report a
    problem with `print_jobs.printer_id` rather than with the rule that was wrong.
    """
    offenders = [
        name
        for name in SET_NULL
        if not Base.metadata.tables[name.split(".")[0]].columns[name.split(".")[1]].nullable
    ]
    assert not offenders, (
        f"SET NULL foreign keys on NOT NULL columns: {offenders}. "
        "Either the column is nullable or the rule is wrong; it cannot be both."
    )
