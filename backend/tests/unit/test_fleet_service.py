"""Fleet service: credentials, telemetry, service cards, and the poller."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import (
    ConnectionMode,
    CreatePrinter,
    CreateServiceOperation,
    FleetService,
    MaintenanceKind,
    MountLot,
)
from printorian.contexts.fleet.models import Printer
from printorian.core.clock import FixedClock
from printorian.core.errors import ConflictError
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.secrets import SecretBox
from printorian.drivers import AmsSlot, PrinterState, Telemetry
from printorian.workers.telemetry import TelemetryPoller
from tests.conftest import ensure_lot

KEY = "a-development-secret-key-not-for-production"


@pytest.fixture
def fleet(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> FleetService:
    return FleetService(db_session, clock, bus, SecretBox(KEY))


def a_printer(**overrides: object) -> CreatePrinter:
    base = {
        "name": "p1s-01",
        "brand": "bambu",
        "serial": "20P6BJ632700731",
        "connection_mode": ConnectionMode.LAN,
        "host": "192.168.0.180",
        "access_code": "03d00058",
        "acquisition_cost": Decimal(200_000),
        "expected_lifetime_hours": 20_000,
        "supports_multi_material": True,
    }
    return CreatePrinter(**{**base, **overrides})  # type: ignore[arg-type]


def telemetry_at(
    moment: datetime, state: PrinterState = PrinterState.PRINTING, **extra: object
) -> Telemetry:
    return Telemetry(printer_id="p", observed_at=moment, state=state, **extra)  # type: ignore[arg-type]


# ------------------------------------------------------- credentials


async def test_the_access_code_is_never_returned(fleet: FleetService) -> None:
    """ADR-0014. The view says whether one is set, and nothing more."""
    view = await fleet.register(a_printer())

    assert view.access_code_set is True
    assert "03d00058" not in view.model_dump_json()


async def test_the_access_code_is_encrypted_in_the_database(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    from sqlalchemy import select

    await fleet.register(a_printer())
    stored = await db_session.scalar(select(Printer))

    assert stored is not None
    assert stored.access_code_encrypted is not None
    assert "03d00058" not in stored.access_code_encrypted
    assert stored.access_code_encrypted.startswith("enc:")


async def test_a_printer_without_a_code_reports_it_as_unset(fleet: FleetService) -> None:
    view = await fleet.register(a_printer(access_code=None, connection_mode=ConnectionMode.MANUAL))
    assert view.access_code_set is False


async def test_replacing_the_code_keeps_it_write_only(fleet: FleetService) -> None:
    view = await fleet.register(a_printer())
    updated = await fleet.set_access_code(view.id, "99887766")

    assert updated.access_code_set is True
    assert "99887766" not in updated.model_dump_json()


async def test_the_plaintext_is_recovered_only_to_build_a_connection(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    from sqlalchemy import select

    await fleet.register(a_printer())
    printer = await db_session.scalar(select(Printer))
    assert printer is not None

    connection = fleet.connection_for(printer)
    assert connection.access_code == "03d00058"
    assert connection.serial == "20P6BJ632700731"


async def test_duplicate_names_are_refused(fleet: FleetService) -> None:
    await fleet.register(a_printer())
    with pytest.raises(ConflictError):
        await fleet.register(a_printer())


# -------------------------------------------------------- telemetry


async def test_recording_telemetry_updates_state_and_slots(fleet: FleetService) -> None:
    view = await fleet.register(a_printer())
    observed = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

    updated = await fleet.record(
        view.id,
        Telemetry(
            printer_id=str(view.id),
            observed_at=observed,
            state=PrinterState.PRINTING,
            progress_percent=42,
            ams_slots=(
                AmsSlot(
                    unit=0, index=3, material_type="PLA", colour_hex="#FFFFFF", remaining_percent=80
                ),
            ),
        ),
    )

    assert updated.state is PrinterState.PRINTING
    assert updated.progress_percent == 42
    assert updated.last_seen_at == observed
    assert len(updated.slots) == 1
    assert updated.slots[0].material_type == "PLA"


async def test_an_eta_is_offered_only_while_printing(fleet: FleetService) -> None:
    view = await fleet.register(a_printer())
    from printorian.core.units import Duration

    printing = await fleet.record(
        view.id,
        telemetry_at(datetime(2026, 3, 2, 9, 0, tzinfo=UTC), remaining=Duration(Decimal(90))),
    )
    assert printing.eta is not None
    assert printing.remaining_minutes == 90

    finished = await fleet.record(
        view.id, telemetry_at(datetime(2026, 3, 2, 10, 0, tzinfo=UTC), PrinterState.FINISHED)
    )
    assert finished.eta is None


async def test_printing_hours_accumulate_from_observed_time(
    fleet: FleetService, clock: FixedClock
) -> None:
    """Amortization must reflect the machine that ran, not the job that was planned."""
    view = await fleet.register(a_printer())
    await fleet.record(view.id, telemetry_at(clock.now()))

    clock.advance(timedelta(hours=2))
    updated = await fleet.record(view.id, telemetry_at(clock.now()))

    assert updated.printed_hours == pytest.approx(Decimal(2), abs=Decimal("0.01"))


async def test_idle_time_does_not_wear_the_machine_out(
    fleet: FleetService, clock: FixedClock
) -> None:
    view = await fleet.register(a_printer())
    await fleet.record(view.id, telemetry_at(clock.now(), PrinterState.IDLE))

    clock.advance(timedelta(hours=8))
    updated = await fleet.record(view.id, telemetry_at(clock.now(), PrinterState.IDLE))

    assert updated.printed_hours == Decimal(0)


async def test_a_state_change_is_published(fleet: FleetService, bus: EventBus) -> None:
    view = await fleet.register(a_printer())

    async with bus.collecting() as events:
        await fleet.record(view.id, telemetry_at(datetime(2026, 3, 2, 9, 0, tzinfo=UTC)))

    assert "fleet.printer_state_changed" in [event.name for event in events]


async def test_an_unchanged_state_is_not_republished(fleet: FleetService, bus: EventBus) -> None:
    """The floor should hear about changes, not about every poll."""
    view = await fleet.register(a_printer())
    await fleet.record(view.id, telemetry_at(datetime(2026, 3, 2, 9, 0, tzinfo=UTC)))

    async with bus.collecting() as events:
        await fleet.record(view.id, telemetry_at(datetime(2026, 3, 2, 9, 1, tzinfo=UTC)))

    assert "fleet.printer_state_changed" not in [event.name for event in events]


async def test_unreachable_is_a_recorded_observation(fleet: FleetService, bus: EventBus) -> None:
    """V1 answered this case with invented data. Here the state changes and an
    event fires, so a machine dropping off the network is noticed."""
    view = await fleet.register(a_printer())
    await fleet.record(view.id, telemetry_at(datetime(2026, 3, 2, 9, 0, tzinfo=UTC)))

    async with bus.collecting() as events:
        offline = await fleet.mark_unreachable(view.id, "error.driver.unavailable")

    assert offline.state is PrinterState.OFFLINE
    assert offline.needs_attention is True
    assert "fleet.printer_unreachable" in [event.name for event in events]


# ------------------------------------------------------ service card


async def test_a_service_becomes_due_after_enough_printing_hours(
    fleet: FleetService, clock: FixedClock
) -> None:
    view = await fleet.register(a_printer())
    view = await fleet.add_service_operation(
        view.id,
        CreateServiceOperation(
            kind=MaintenanceKind.NOZZLE_CHANGE, interval_hours=2, materials_used=["nozzle-0.4"]
        ),
    )
    assert view.services[0].is_due is False

    await fleet.record(view.id, telemetry_at(clock.now()))
    clock.advance(timedelta(hours=3))
    view = await fleet.record(view.id, telemetry_at(clock.now()))

    assert view.services[0].is_due is True
    assert view.maintenance_due is True
    assert view.needs_attention is True


async def test_completing_a_service_resets_its_clock(
    fleet: FleetService, clock: FixedClock
) -> None:
    view = await fleet.register(a_printer())
    view = await fleet.add_service_operation(
        view.id, CreateServiceOperation(kind=MaintenanceKind.BED_LEVEL, interval_hours=1)
    )
    await fleet.record(view.id, telemetry_at(clock.now()))
    clock.advance(timedelta(hours=5))
    view = await fleet.record(view.id, telemetry_at(clock.now()))
    assert view.services[0].is_due is True

    view = await fleet.complete_service(view.id, view.services[0].id)
    assert view.services[0].is_due is False
    assert view.services[0].last_done_at is not None


async def test_amortization_is_reported_per_printing_hour(fleet: FleetService) -> None:
    view = await fleet.register(a_printer())
    assert view.amortization_per_hour == Decimal("10.00")


# --------------------------------------------------------- the table


async def test_the_table_counts_every_state(fleet: FleetService) -> None:
    await fleet.register(a_printer())
    table = await fleet.table()

    assert table.total == 1
    assert {entry.state for entry in table.counts} == set(PrinterState)


async def test_the_table_counts_machines_needing_attention(fleet: FleetService) -> None:
    view = await fleet.register(a_printer())
    await fleet.mark_unreachable(view.id, "error.driver.unavailable")

    table = await fleet.table()
    assert table.attention == 1


async def test_mounting_a_lot_records_where_it_physically_is(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    """The scenario's second location kind: printer + AMS port."""
    view = await fleet.register(a_printer())
    lot = new_id()
    # `ams_slots.lot_id` is a real foreign key.
    await ensure_lot(db_session, lot)

    updated = await fleet.mount_lot(view.id, MountLot(unit=0, index=2, lot_id=lot))
    slot = next(s for s in updated.slots if s.index == 2)
    assert slot.lot_id == lot


# ------------------------------------------------------------ poller


async def test_the_poller_skips_manual_printers(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    """There is nothing to ask a human-driven machine, and inventing a poll for it
    would either erase what an operator declared or fabricate a reading."""
    from sqlalchemy import select

    await fleet.register(
        a_printer(name="manual-01", connection_mode=ConnectionMode.MANUAL, access_code=None)
    )
    printers = list(await db_session.scalars(select(Printer)))

    outcome = await TelemetryPoller(fleet, {}).sweep(printers)

    assert outcome.skipped == 1
    assert outcome.polled == 0


async def test_a_printer_with_no_driver_is_marked_offline(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    from sqlalchemy import select

    view = await fleet.register(a_printer())
    printers = list(await db_session.scalars(select(Printer)))

    outcome = await TelemetryPoller(fleet, {}).sweep(printers)

    assert outcome.unreachable == 1
    assert (await fleet.get(view.id)).state is PrinterState.OFFLINE
