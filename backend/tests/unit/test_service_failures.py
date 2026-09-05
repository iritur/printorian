"""Opening, closing and refusing a failure record.

Two of these assertions are about PostgreSQL rather than about Python, and they are
issued as raw SQL for the reason `test_delete_rules.py` sets out at length: if the
statement went through `ServiceDesk`, the refusal under test would be the one
written in the method above it, and every assertion here would pass against a
database holding no constraint at all. That is precisely the state these exist to
detect — the partial unique index is what makes `workers/service.py` idempotent
across two worker processes, and no amount of Python can stand in for it.

The refusals that *are* Python are here too, next to their constraints rather than
in a separate file, because a reader asking "what stops a second restore" needs both
answers in one place: a code the client can act on (ADR-0012), and a CHECK that
still holds when the writer is a psql session.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.service import FailureCause, FailureOrigin, ServiceDesk
from printorian.core.clock import FixedClock
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId, new_id
from tests.unit._measure_support import HOUR
from tests.unit._service_support import a_failure, a_printer

#: An insert that goes nowhere near the ORM. Every column the table requires and
#: nothing else, with the ids cast rather than adapted, so this statement means the
#: same thing whether or not the application is running.
_RAW_INSERT = text(
    """
INSERT INTO printer_failures (id, printer_id, origin, detected_at)
VALUES (CAST(:id AS uuid), CAST(:printer_id AS uuid), :origin, CAST(:detected_at AS timestamptz))
"""
)


@pytest.fixture
def desk(db_session: AsyncSession, clock: FixedClock) -> ServiceDesk:
    return ServiceDesk(db_session, clock)


async def _raw_open(db: AsyncSession, printer_id: EntityId) -> None:
    await db.execute(
        _RAW_INSERT,
        {
            "id": str(new_id()),
            "printer_id": str(printer_id),
            "origin": FailureOrigin.DRIVER.value,
            "detected_at": HOUR.isoformat(),
        },
    )


# -------------------------------------------------- what the database refuses


async def test_a_second_open_failure_for_one_printer_is_refused(
    db_session: AsyncSession, desk: ServiceDesk
) -> None:
    """``uq_printer_failures_open``, exercised where it actually lives.

    This is the guard the sweep's idempotence rests on. `ServiceSweep` reads the
    open failures before it inserts, and that read races with its own next pass and
    with a second worker process; the index is what refuses when the read loses.
    Drop it and `test_a_second_sweep_does_not_open_a_second_failure` still passes,
    because the Python check is doing the work — which is why this insert bypasses
    every line of Python the application owns.
    """
    printer = await a_printer(db_session)
    await a_failure(desk, printer, at=HOUR)

    with pytest.raises(IntegrityError):
        await _raw_open(db_session, printer)
    await db_session.rollback()


async def test_a_machine_whose_failure_was_closed_may_break_again(
    db_session: AsyncSession, desk: ServiceDesk
) -> None:
    """The index is *partial*, and this is the half that says so.

    A machine that broke, was repaired and broke again is the ordinary case, not an
    error. A plain unique index on `printer_id` would pass the test above and refuse
    this one — silently capping every machine's history at a single failure for
    ever.
    """
    printer = await a_printer(db_session)
    await a_failure(desk, printer, at=HOUR, restored_at=HOUR + timedelta(minutes=10))

    await _raw_open(db_session, printer)  # no refusal

    assert await db_session.scalar(text("SELECT count(*) FROM printer_failures")) == 2


async def test_a_restore_earlier_than_the_detection_is_refused_by_the_database(
    db_session: AsyncSession, desk: ServiceDesk
) -> None:
    """``ck_printer_failures_restored_after_detected``, without the service.

    The method below refuses this with a code, and that refusal only exists while
    the application is the writer. A negative repair time reaching the table would
    be invisible afterwards: MTTR is a mean, which is exactly the shape that hides
    one bad row.
    """
    printer = await a_printer(db_session)
    failure = await a_failure(desk, printer, at=HOUR)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE printer_failures SET restored_at = CAST(:at AS timestamptz) "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"at": (HOUR - timedelta(minutes=1)).isoformat(), "id": str(failure)},
        )
    await db_session.rollback()


# ------------------------------------------------------ what the service refuses


async def test_a_restore_earlier_than_the_detection_is_refused_with_a_code(
    db_session: AsyncSession, desk: ServiceDesk
) -> None:
    """The same rule as a code the client can render (ADR-0012)."""
    printer = await a_printer(db_session)
    failure = await a_failure(desk, printer, at=HOUR)

    with pytest.raises(DomainRuleViolationError) as refused:
        await desk.restore(failure, HOUR - timedelta(minutes=1))

    assert refused.value.code == "error.service.restored_before_detected"
    assert refused.value.details["detected_at"] == HOUR.isoformat()


async def test_restoring_an_already_restored_failure_is_refused(
    db_session: AsyncSession, desk: ServiceDesk
) -> None:
    """The irreversible-ish path, and the reason it is worth a test of its own.

    `restored_at` is the farm's single measurement of how long this machine was
    down. A second restore does not fail loudly — it overwrites, lengthening a
    repair that had already ended, and nothing afterwards can tell that it happened.
    The refusal carries the moment already recorded so the caller can see what it
    was about to replace.
    """
    printer = await a_printer(db_session)
    repaired = HOUR + timedelta(minutes=20)
    failure = await a_failure(desk, printer, at=HOUR, restored_at=repaired)

    with pytest.raises(DomainRuleViolationError) as refused:
        await desk.restore(failure, HOUR + timedelta(hours=1))

    assert refused.value.code == "error.service.already_restored"
    assert refused.value.details["restored_at"] == repaired.isoformat()


async def test_an_unknown_failure_is_not_found_rather_than_silently_ignored(
    db_session: AsyncSession, desk: ServiceDesk
) -> None:
    """ADR-0007's unknown-id rule: a 404, never a success over nothing."""
    with pytest.raises(NotFoundError) as missing:
        await desk.restore(new_id(), HOUR)

    assert missing.value.code == "error.service.failure_not_found"


# --------------------------------------------------------------- naming a cause


async def test_a_driver_opened_failure_gets_its_cause_from_a_person(
    db_session: AsyncSession, desk: ServiceDesk
) -> None:
    """The only route by which a cause is ever written for a driver's report.

    The record keeps the vendor code it was opened with — naming the cause does not
    erase what the machine said, because the two are different claims and only one
    of them is a measurement.
    """
    printer = await a_printer(db_session)
    failure = await desk.record(
        printer,
        FailureOrigin.DRIVER,
        error_code="bambu.print_error.0300_8003",
        detected_at=HOUR,
    )
    assert failure.cause is None

    named = await desk.set_cause(failure.id, FailureCause.FILAMENT_BREAK)

    assert named.cause is FailureCause.FILAMENT_BREAK
    assert named.error_code == "bambu.print_error.0300_8003"
