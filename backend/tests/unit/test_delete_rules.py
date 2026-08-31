"""What each delete rule actually does, against real rows.

`tests/test_referential_integrity.py` holds the inventory: which of the
forty-eight foreign keys is ``CASCADE``, which is ``SET NULL``, which is
``RESTRICT``, and whether PostgreSQL is holding the rule the model asked for. That
is a catalogue comparison, and a catalogue is not behaviour (CLAUDE.md §2) — it
would go on passing if ``RESTRICT`` did something other than what
`docs/DATABASE-REVIEW.md` §3 assumes it does.

So one representative of each category is exercised here, chosen for consequence
rather than for coverage:

- the ``RESTRICT`` that model retention depends on, which is the only irreversible
  path any of these rules guards;
- the two other ``RESTRICT``s standing in front of money — a payment whose order
  was deleted, and a price whose rates were;
- the ``CASCADE`` that takes an order's lines with the order;
- the ``SET NULL`` that lets a printer be retired without deleting the jobs it ran.

**Every delete below is issued as SQL rather than through `session.delete`, and
that is not a style choice.** `Order.lines` and `Order.events` carry
``cascade="all, delete-orphan"``, so the ORM would delete the children itself, in
Python, and every assertion here would pass with no constraint in the database at
all — which is exactly the state these tests exist to detect.

The last test is what the whole of issue #47 is about: a job built against an order
that does not exist. Dozens of tests were written in that state under SQLite, which
enforces no foreign key at all; `tests/factories.py` is what gives them real parents
now, and this is the assertion that says the enforcement is switched on.

That last point is why these live apart from the inventory rather than beside it.
A constraint can be present and not enforced — one ``SET session_replication_role =
replica`` in a fixture does exactly that — and in that state every assertion in
`test_referential_integrity.py` still passes while all six of these fail. Measured,
not assumed: the mutation was run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.core.ids import EntityId, new_id


async def _an_order(session: AsyncSession, *, number: str = "REF-1") -> EntityId:
    from printorian.contexts.ordering.models import Order

    order = Order(id=new_id(), number=number)
    session.add(order)
    await session.flush()
    return order.id


async def _some_geometry(session: AsyncSession, *, sha: str = "a" * 64) -> EntityId:
    from printorian.contexts.catalog.models import ModelAsset

    asset = ModelAsset(id=new_id(), sha256=sha, last_used_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC))
    session.add(asset)
    await session.flush()
    return asset.id


async def _a_line(session: AsyncSession, order_id: EntityId, asset_id: EntityId | None) -> None:
    from printorian.contexts.ordering.models import OrderLine

    session.add(
        OrderLine(
            id=new_id(),
            order_id=order_id,
            model_asset_id=asset_id,
            model_name="bracket.stl",
            material_code="PLA",
            estimated_minutes=Decimal(120),
            estimated_grams=Decimal(50),
        )
    )
    await session.flush()


async def test_geometry_an_order_still_needs_cannot_be_deleted(db_session: AsyncSession) -> None:
    """The ``RESTRICT`` that model retention depends on, exercised.

    `catalog.assets`' sweep deletes assets whose ``last_used_at`` is past the
    retention window, and asks `ordering` nothing at all — which is only safe
    because the database refuses. Without the rule, an order placed against a model
    nobody has re-quoted for ninety days loses the geometry it is waiting to print,
    irreversibly, and the sweep reports success.
    """
    from printorian.contexts.catalog.models import ModelAsset

    order_id = await _an_order(db_session)
    asset_id = await _some_geometry(db_session)
    await _a_line(db_session, order_id, asset_id)

    with pytest.raises(IntegrityError):
        await db_session.execute(delete(ModelAsset).where(ModelAsset.id == asset_id))
    await db_session.rollback()


async def test_an_order_with_money_against_it_cannot_be_deleted(db_session: AsyncSession) -> None:
    """``payments.order_id`` is ``RESTRICT``: a payment must stay explicable.

    A payment whose order had been deleted is a sum of money in the ledger with
    nothing saying what it bought — unreconcilable against the gateway, and
    unanswerable to the customer who paid it.
    """
    from printorian.contexts.ordering.models import Order
    from printorian.contexts.payments.models import Payment

    order_id = await _an_order(db_session)
    db_session.add(
        Payment(
            id=new_id(),
            order_id=order_id,
            provider="mock",
            idempotency_key="ref-1",
            amount=Decimal("100.00"),
        )
    )
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(delete(Order).where(Order.id == order_id))
    await db_session.rollback()


async def test_the_rates_a_price_was_computed_from_cannot_be_deleted(
    db_session: AsyncSession,
) -> None:
    """``orders.rate_snapshot_id`` is ``RESTRICT``, which is what makes ADR-0002 a fact.

    The promise is that an order's price can be recomputed years later. The hash on
    the order proves *which* rates were used; the snapshot row is the only place the
    values behind it exist. A cleanup dropping unreferenced snapshots must not be
    able to take a referenced one with it.
    """
    from printorian.contexts.ordering.models import Order, RateSnapshotRecord

    # Flushed in two steps, and it has to be. The unit of work does not order these
    # two inserts by the foreign key between them — `rate_snapshot_id` is a column
    # reference with no relationship behind it — so a single flush writes the order
    # first and fails on the constraint this test has not got to yet.
    db_session.add(RateSnapshotRecord(id="deadbeef", payload={"margin": "0.25"}))
    await db_session.flush()
    db_session.add(Order(id=new_id(), number="REF-RATES", rate_snapshot_id="deadbeef"))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            delete(RateSnapshotRecord).where(RateSnapshotRecord.id == "deadbeef")
        )
    await db_session.rollback()


async def test_deleting_an_order_takes_its_lines_with_it(db_session: AsyncSession) -> None:
    """``order_lines.order_id`` is ``CASCADE``, and the database does it, not the ORM.

    A line left behind is a configured model belonging to no order: it appears on no
    screen, is fixed by nobody, and counts toward every aggregate over the table.
    """
    from printorian.contexts.ordering.models import Order, OrderLine

    order_id = await _an_order(db_session, number="REF-CASCADE")
    await _a_line(db_session, order_id, None)

    await db_session.execute(delete(Order).where(Order.id == order_id))
    await db_session.flush()

    remaining = await db_session.scalars(select(OrderLine).where(OrderLine.order_id == order_id))
    assert remaining.all() == []


async def test_retiring_a_printer_keeps_the_jobs_it_ran(db_session: AsyncSession) -> None:
    """``print_jobs.printer_id`` is ``SET NULL``: the history survives the machine.

    The whole of production history hangs off machines that will eventually be sold
    or scrapped. ``CASCADE`` here would make decommissioning a printer delete every
    job it ever ran, and with it the throughput figures, the variances and the
    assignment records that explain them. The job stays; only the reference goes,
    and a null there reads as "no machine recorded any more" rather than as a job
    that ran nowhere (ADR-0007).
    """
    from printorian.contexts.fleet.models import Printer
    from printorian.contexts.production.models import PrintJob

    order_id = await _an_order(db_session, number="REF-SETNULL")
    printer_id, job_id = new_id(), new_id()
    db_session.add(Printer(id=printer_id, name="P-RETIRED"))
    await db_session.flush()
    db_session.add(PrintJob(id=job_id, order_id=order_id, printer_id=printer_id))
    await db_session.flush()

    await db_session.execute(delete(Printer).where(Printer.id == printer_id))
    await db_session.flush()
    # The session still holds the job as it was written; `SET NULL` happened in the
    # database, behind the identity map's back. Without this the assertion below
    # reads a cached row and passes whatever the database actually did.
    #
    # The ids are kept in local variables for the same reason: reading `job.id` back
    # off an expired instance is a lazy refresh, and a lazy refresh from a synchronous
    # attribute access under asyncpg is `MissingGreenlet`, not a query.
    db_session.expire_all()

    survivor = await db_session.get(PrintJob, job_id)
    assert survivor is not None
    assert survivor.printer_id is None


async def test_a_job_cannot_be_built_against_an_order_that_does_not_exist(
    db_session: AsyncSession,
) -> None:
    """The state issue #47 is about, asserted as impossible.

    Under SQLite this insert succeeded, and the tests around it asserted about a
    world production cannot reach: a job exists only because an order produced it.
    Kept as a test of its own rather than left implicit in the others, because it is
    the single fact `tests/factories.py` was written to satisfy — and if enforcement
    ever lapses, this should say so by name rather than be inferred from a hundred
    unrelated failures somewhere else.
    """
    from printorian.contexts.production.models import PrintJob

    db_session.add(PrintJob(id=new_id(), order_id=new_id()))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
