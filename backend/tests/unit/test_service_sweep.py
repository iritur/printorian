"""The «СООБЩИЛ ДРАЙВЕР» path: what the sweep opens, what it closes, and what it
deliberately leaves alone.

Three of these are about an *absence* — a failure the sweep must not open, a repair
it must not record, a duplicate it must not mint — and absences are the assertions
that rot first, because deleting the line that produces them breaks nothing visible.
Each one names the line it guards in its docstring so the connection survives.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.models import Printer
from printorian.contexts.service import FailureOrigin, ServiceDesk
from printorian.contexts.service.models import PrinterFailure
from printorian.core.clock import FixedClock
from printorian.core.ids import EntityId
from printorian.drivers import PrinterState
from printorian.workers.service import ServiceSweep, SweepOutcome
from tests.unit._measure_support import HOUR
from tests.unit._service_support import a_failure, a_printer

CODE = "bambu.print_error.0300_8003"

#: The observation that saw the machine break, and the later one that saw it
#: working. Both are before `FROZEN_NOW`, so a `restored_at` taken from the clock
#: instead of from the machine is a different number and the test can tell.
BROKE_AT = HOUR
CAME_BACK_AT = HOUR + timedelta(minutes=40)


@pytest.fixture
def desk(db_session: AsyncSession, clock: FixedClock) -> ServiceDesk:
    return ServiceDesk(db_session, clock)


@pytest.fixture
def sweep(db_session: AsyncSession, desk: ServiceDesk) -> ServiceSweep:
    return ServiceSweep(db_session, desk)


async def failures_of(db: AsyncSession, printer_id: EntityId) -> list[PrinterFailure]:
    rows = await db.scalars(
        select(PrinterFailure)
        .where(PrinterFailure.printer_id == printer_id)
        .order_by(PrinterFailure.detected_at)
    )
    return list(rows)


async def working(db: AsyncSession, printer_id: EntityId, *, at: datetime) -> None:
    """Record that a later observation found the machine idle.

    Written onto the row rather than through `FleetService.record`, because what the
    sweep reads is the registry row and this test is about the sweep. The telemetry
    path that produces the row is covered in `test_fleet_service.py`.
    """
    printer = await db.get(Printer, printer_id)
    assert printer is not None
    printer.state = PrinterState.IDLE
    printer.last_seen_at = at
    await db.flush()


# ------------------------------------------------------------------- opening


async def test_a_printer_reported_in_error_opens_a_failure_carrying_the_drivers_code(
    db_session: AsyncSession, sweep: ServiceSweep
) -> None:
    """The record is dated from the machine's observation, not from this pass.

    `detected_at` is `last_seen_at`. Using `clock.now()` would fold up to a whole
    sweep interval of downtime into every failure and make a measured figure move
    when somebody changes `service_sweep_seconds` (ADR-0007) — which is why the
    fixture's observation is deliberately an hour before `FROZEN_NOW`.
    """
    printer = await a_printer(
        db_session, state=PrinterState.ERROR, last_seen_at=BROKE_AT, error_code=CODE
    )

    outcome = await sweep.sweep()

    assert outcome == SweepOutcome(opened=1)
    (failure,) = await failures_of(db_session, printer)
    assert failure.origin is FailureOrigin.DRIVER
    assert failure.error_code == CODE
    assert failure.detected_at == BROKE_AT


async def test_a_driver_failure_opens_with_no_cause(
    db_session: AsyncSession, sweep: ServiceSweep
) -> None:
    """ADR-0007, in the place it is easiest to break.

    `bambu.print_error.0300_8003` is a code, and the farm holds no table turning one
    into «слом филамента». Guessing would put a bar in the «Причины отказов» funnel
    that nobody measured — and the funnel's whole value is that its bars mean what
    they say.
    """
    printer = await a_printer(
        db_session, state=PrinterState.ERROR, last_seen_at=BROKE_AT, error_code=CODE
    )

    await sweep.sweep()

    (failure,) = await failures_of(db_session, printer)
    assert failure.cause is None


async def test_a_second_sweep_does_not_open_a_second_failure(
    db_session: AsyncSession, sweep: ServiceSweep
) -> None:
    """Idempotence, which is what makes a reconciling pass safe to run every minute.

    Held here by the read in `ServiceSweep.sweep`, and in the database by
    `uq_printer_failures_open` — `test_service_failures.py` proves the second half,
    because this test alone would go on passing with the index dropped.
    """
    printer = await a_printer(
        db_session, state=PrinterState.ERROR, last_seen_at=BROKE_AT, error_code=CODE
    )

    first = await sweep.sweep()
    second = await sweep.sweep()

    assert (first.opened, second.opened) == (1, 0)
    assert len(await failures_of(db_session, printer)) == 1


async def test_an_unreachable_printer_does_not_become_a_failure(
    db_session: AsyncSession, sweep: ServiceSweep
) -> None:
    """The `OFFLINE` exclusion, and the reason it is a judgement rather than an
    oversight.

    `mark_unreachable` writes `OFFLINE` for a poll that did not answer, so this is
    the state a switch reboot — or a restart of the worker process itself — puts the
    whole farm into. Counting it would mint a failure per machine and drive MTTR
    from repairs nobody made. Add `OFFLINE` to `FAILING_STATES` and this is the only
    test that notices.
    """
    printer = await a_printer(db_session, state=PrinterState.OFFLINE, last_seen_at=BROKE_AT)

    outcome = await sweep.sweep()

    assert outcome.opened == 0
    assert await failures_of(db_session, printer) == []


async def test_a_retired_machine_is_left_alone(
    db_session: AsyncSession, sweep: ServiceSweep
) -> None:
    """A printer that has left the farm keeps its last state for ever.

    Opening a failure against it would file a ticket about hardware nobody can go
    and look at, and nothing would ever close it — an open failure that never ends
    is a machine that reads as permanently broken.
    """
    printer = await a_printer(
        db_session, state=PrinterState.ERROR, last_seen_at=BROKE_AT, is_active=False
    )

    await sweep.sweep()

    assert await failures_of(db_session, printer) == []


# ------------------------------------------------------------------- closing


async def test_a_printer_that_came_back_closes_its_failure_at_the_observation_that_saw_it(
    db_session: AsyncSession, sweep: ServiceSweep
) -> None:
    """`restored_at` is the observation, not the sweep.

    `clock.now()` here would add up to one interval of downtime that nobody
    observed, to a figure MTTR is computed from. The two moments are deliberately
    different in this test, so the wrong one cannot pass.
    """
    printer = await a_printer(
        db_session, state=PrinterState.ERROR, last_seen_at=BROKE_AT, error_code=CODE
    )
    await sweep.sweep()

    await working(db_session, printer, at=CAME_BACK_AT)
    outcome = await sweep.sweep()

    assert outcome.closed == 1
    (failure,) = await failures_of(db_session, printer)
    assert failure.restored_at == CAME_BACK_AT


async def test_an_unreachable_printer_does_not_close_a_failure(
    db_session: AsyncSession, sweep: ServiceSweep
) -> None:
    """The mirrored half of the `OFFLINE` judgement, and the easier one to lose.

    A machine that broke and then stopped answering has not been seen working, and
    `mark_unreachable` does not advance `last_seen_at` — so closing here would write
    a repair time out of an observation that saw nothing, and would end the outage
    at the moment it began.
    """
    printer = await a_printer(
        db_session, state=PrinterState.ERROR, last_seen_at=BROKE_AT, error_code=CODE
    )
    await sweep.sweep()

    row = await db_session.get(Printer, printer)
    assert row is not None
    row.state = PrinterState.OFFLINE
    await db_session.flush()
    outcome = await sweep.sweep()

    assert outcome.closed == 0
    (failure,) = await failures_of(db_session, printer)
    assert failure.restored_at is None


async def test_a_failure_a_person_recorded_is_not_closed_by_the_machine(
    db_session: AsyncSession, sweep: ServiceSweep, desk: ServiceDesk
) -> None:
    """A hand-written failure describes something the machine never reported.

    A jammed extruder the printer happily calls `IDLE` is the ordinary case, so its
    own state is no evidence the problem is over. The person who opened the record
    is the one who closes it.
    """
    printer = await a_printer(db_session, state=PrinterState.IDLE, last_seen_at=CAME_BACK_AT)
    await a_failure(desk, printer, at=BROKE_AT, origin=FailureOrigin.PERSON)

    outcome = await sweep.sweep()

    assert outcome.closed == 0
    (failure,) = await failures_of(db_session, printer)
    assert failure.restored_at is None
