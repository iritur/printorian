"""Whether the farm can tell that something is on a machine before power is cut.

Split the way `test_assignment_record_growth.py` is split, and for the same reason:
the decision is a pure function over a reading, so these cases drive the real
comparison rather than a copy of it, while the reading itself is exercised against
real PostgreSQL because the half that can silently break is whether the query means
what the code thinks it means.

The case worth reading twice is `test_a_plan_does_not_veto_a_reboot`. ASSIGNED
occupies a printer by `JobStatus.occupies_printer` and still does not stop a
reboot — that is a deliberate divergence from a set that happens to hold the same
members, and if somebody "simplifies" the verdict to use it, this file says why not.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.inflight import (
    InFlight,
    in_flight,
    interrupting_is_unsafe,
)
from printorian.contexts.production.policies import JobStatus
from tests.unit._dashboard_support import a_job, an_order_id

# ------------------------------------------------------------ the decision


def test_an_idle_farm_may_be_interrupted() -> None:
    assert interrupting_is_unsafe(InFlight(assigned=0, dispatching=0, printing=0)) is False


def test_a_running_print_vetoes_the_interruption() -> None:
    """The case the whole change exists for."""
    assert interrupting_is_unsafe(InFlight(assigned=0, dispatching=0, printing=1)) is True


def test_a_half_finished_upload_vetoes_the_interruption() -> None:
    """DISPATCHING alone, which issue #17's own wording — "while any job is
    printing" — would have let through. `policies.py` gives the state its own name
    precisely so a dead upload cannot be mistaken for queued or for printing, and a
    machine left holding half a plate file is the one genuinely irreversible moment
    in the sequence."""
    assert interrupting_is_unsafe(InFlight(assigned=0, dispatching=1, printing=0)) is True


def test_a_plan_does_not_veto_a_reboot() -> None:
    """ASSIGNED alone is safe, although the job "occupies" a printer.

    Nothing has been sent to a machine, and `TRANSITIONS` lets an assignment go back
    to READY for exactly that reason — the planner re-makes it after a restart. The
    alternative is a job wedged in ASSIGNED holding the veto on for ever, which
    would mean a host that never patches again: the same permanently-red failure
    `/health/workers` refuses to accept from an unreachable printer.
    """
    assert interrupting_is_unsafe(InFlight(assigned=3, dispatching=0, printing=0)) is False


def test_a_plan_alongside_a_print_is_still_unsafe() -> None:
    """The exclusion above is not a veto of its own: ASSIGNED abstains, it does not
    vote "safe" over a print that is running beside it."""
    assert interrupting_is_unsafe(InFlight(assigned=3, dispatching=0, printing=1)) is True


# ------------------------------------------------------------ against the table


async def test_the_reading_counts_each_stage_separately(db_session: AsyncSession) -> None:
    """Per status, so a deferral can say which stage caused it.

    A single total would pass a test like this too, and would leave an operator at
    06:00 unable to tell one plate at 40% from a job stuck in DISPATCHING since
    Friday — two situations wanting opposite actions.
    """
    order_id = await an_order_id(db_session)
    staged = ((JobStatus.ASSIGNED, 1), (JobStatus.DISPATCHING, 2), (JobStatus.PRINTING, 3))
    for status, many in staged:
        for _ in range(many):
            db_session.add(a_job(order_id, status=status, grams=Decimal(10)))
    await db_session.commit()

    reading = await in_flight(db_session)

    assert reading == InFlight(assigned=1, dispatching=2, printing=3)


async def test_an_empty_table_reads_as_a_measured_zero(db_session: AsyncSession) -> None:
    """Zero here is honest, and it is honest only because the query ran.

    A status with no rows is absent from the grouped result, and filling it with 0
    is the one place in this module where that is the truth rather than an
    invention. The unreadable case is not this case — it never reaches a reading at
    all, and `/health/printing` answers `unknown` for it.
    """
    assert await in_flight(db_session) == InFlight(assigned=0, dispatching=0, printing=0)


async def test_a_print_whose_printer_was_cleared_is_still_counted(
    db_session: AsyncSession,
) -> None:
    """The test that fails the moment somebody narrows the query with
    `printer_id IS NOT NULL`.

    `print_jobs.printer_id` is `SET NULL`, so retiring a printer under a running job
    leaves a PRINTING row naming no machine. It is still a print. Dropping it would
    read an absent value as a zero on the one path where a zero authorises pulling
    the plug.
    """
    order_id = await an_order_id(db_session)
    db_session.add(a_job(order_id, status=JobStatus.PRINTING, grams=Decimal(10), printer_id=None))
    await db_session.commit()

    assert (await in_flight(db_session)).printing == 1


async def test_nothing_off_a_machine_is_counted(db_session: AsyncSession) -> None:
    """Everything before a machine and everything after it stays out of all three
    counts. A finished farm reads as idle, and a queue full of unsliced work is not
    a reason to keep a host on stale kernels for a week."""
    order_id = await an_order_id(db_session)
    for status in (
        JobStatus.PENDING,
        JobStatus.ON_HOLD,
        JobStatus.READY,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    ):
        db_session.add(a_job(order_id, status=status, grams=Decimal(10)))
    await db_session.commit()

    reading = await in_flight(db_session)

    assert reading == InFlight(assigned=0, dispatching=0, printing=0)
    assert interrupting_is_unsafe(reading) is False
