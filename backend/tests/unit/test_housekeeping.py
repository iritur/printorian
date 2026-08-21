"""Housekeeping: expired sessions are deleted, telemetry history is kept.

Both are tables that previously grew without bound in one direction or held no
history at all in the other, and both are things nothing tested because nothing
did them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import FleetService
from printorian.contexts.fleet.history import TelemetrySample
from printorian.contexts.fleet.schemas import CreatePrinter
from printorian.contexts.identity import IdentityService
from printorian.contexts.identity.models import Session
from printorian.contexts.identity.schemas import CreateUser, SignIn
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.secrets import SecretBox
from printorian.core.units import Duration
from printorian.drivers import PrinterState, Telemetry

FROZEN_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def identity(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> IdentityService:
    return IdentityService(db_session, settings, clock, bus)


@pytest.fixture
def fleet(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> FleetService:
    return FleetService(
        db_session, clock, bus, SecretBox("unit-test-key-long-enough-for-the-guard")
    )


# ------------------------------------------------------ session reaper


async def test_a_live_session_is_never_reaped(
    identity: IdentityService, db_session: AsyncSession
) -> None:
    await identity.create_user(
        CreateUser(email="live@example.com", display_name="Live", password="correct-horse")
    )
    await identity.sign_in(SignIn(email="live@example.com", password="correct-horse"))

    purged = await identity.purge_expired_sessions()

    assert purged == 0
    assert await db_session.scalar(select(func.count()).select_from(Session)) == 1


async def test_a_long_expired_session_is_deleted(
    identity: IdentityService, db_session: AsyncSession, clock: FixedClock
) -> None:
    """The table has never had a row removed from it. Now it does.

    With a twelve-hour TTL and no reaper, `sessions` grows for the life of the
    deployment — every sign-in, forever — on the table the authentication path
    reads on every request.
    """
    await identity.create_user(
        CreateUser(email="old@example.com", display_name="Old", password="correct-horse")
    )
    await identity.sign_in(SignIn(email="old@example.com", password="correct-horse"))

    # Far enough past expiry that the grace period has also elapsed.
    clock.set(FROZEN_NOW + timedelta(days=30))
    purged = await identity.purge_expired_sessions(grace=timedelta(days=7))

    assert purged == 1
    assert await db_session.scalar(select(func.count()).select_from(Session)) == 0


async def test_a_recently_expired_session_is_kept_for_the_grace_period(
    identity: IdentityService, clock: FixedClock
) -> None:
    """Expired-but-recent stays readable, so "your session ended" is still
    distinguishable from "that token was never real"."""
    await identity.create_user(
        CreateUser(email="recent@example.com", display_name="Recent", password="correct-horse")
    )
    await identity.sign_in(SignIn(email="recent@example.com", password="correct-horse"))

    clock.set(FROZEN_NOW + timedelta(days=1))
    assert await identity.purge_expired_sessions(grace=timedelta(days=7)) == 0


# ---------------------------------------------------- telemetry history


async def test_recording_telemetry_keeps_a_sample(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    """`last_telemetry` answers "now"; only a sample can answer "last Tuesday"."""
    printer = await fleet.register(CreatePrinter(name="P1S-01"))

    await fleet.record(
        printer.id,
        Telemetry(
            printer_id=str(printer.id),
            observed_at=FROZEN_NOW,
            state=PrinterState.PRINTING,
            progress_percent=42,
            nozzle_temp_c=Decimal("218.5"),
            remaining=Duration(90),
        ),
    )

    sample = await db_session.scalar(select(TelemetrySample))
    assert sample is not None
    assert sample.printer_id == printer.id
    assert sample.state is PrinterState.PRINTING
    assert sample.progress_percent == 42
    assert sample.nozzle_temp_c == Decimal("218.50")
    assert sample.remaining_minutes == Decimal(90)
    assert sample.observed_at == FROZEN_NOW


async def test_every_observation_is_kept_not_overwritten(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    """Three polls, three rows — the whole difference from `last_telemetry`."""
    printer = await fleet.register(CreatePrinter(name="P1S-02"))

    for index, percent in enumerate((10, 20, 30)):
        await fleet.record(
            printer.id,
            Telemetry(
                printer_id=str(printer.id),
                observed_at=FROZEN_NOW + timedelta(minutes=index),
                state=PrinterState.PRINTING,
                progress_percent=percent,
            ),
        )

    samples = list(await db_session.scalars(select(TelemetrySample)))
    assert sorted(s.progress_percent for s in samples if s.progress_percent) == [10, 20, 30]


async def test_an_absent_reading_is_stored_as_null_not_zero(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    """ADR-0007 applies to the history too.

    A machine that did not report a bed temperature must not leave a zero behind:
    once the reading is old enough that nobody remembers, a column of zeroes is
    indistinguishable from a genuinely cold bed.
    """
    printer = await fleet.register(CreatePrinter(name="P1S-03"))

    await fleet.record(
        printer.id,
        Telemetry(
            printer_id=str(printer.id),
            observed_at=FROZEN_NOW,
            state=PrinterState.IDLE,
        ),
    )

    sample = await db_session.scalar(select(TelemetrySample))
    assert sample is not None
    assert sample.bed_temp_c is None
    assert sample.nozzle_temp_c is None
    assert sample.remaining_minutes is None
    assert sample.progress_percent is None
