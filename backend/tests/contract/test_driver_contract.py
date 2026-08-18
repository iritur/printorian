"""The contract every printer driver must satisfy.

Any new brand adapter is added to ``_drivers`` and must pass this file unchanged.
That is what stops the driver abstraction quietly becoming Bambu-shaped, and it is
the gate a driver has to clear before it may merge (ARCHITECTURE §11).

The rules in the second half of this file are the ones V1 broke.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import ConfigurationError
from printorian.core.units import Duration
from printorian.drivers import (
    ConnectionInfo,
    ConnectionMode,
    DriverError,
    DriverRejectedError,
    DriverStorageError,
    DriverUnavailableError,
    ManualPrinterDriver,
    MockBehaviour,
    MockPrinterDriver,
    PlateUpload,
    PrinterDriver,
    PrinterState,
    available,
    build,
)

PLATE = PlateUpload(filename="plate.3mf", content=b"3mf-bytes", ams_mapping={0: 0})


def _mock(clock: FixedClock, settings: Settings) -> PrinterDriver:
    return MockPrinterDriver(
        ConnectionInfo(printer_id="mock-1", mode=ConnectionMode.MOCK), clock, settings
    )


def _manual(clock: FixedClock, settings: Settings) -> PrinterDriver:
    return ManualPrinterDriver(
        ConnectionInfo(printer_id="manual-1", mode=ConnectionMode.MANUAL), clock
    )


_drivers: dict[str, Callable[[FixedClock, Settings], PrinterDriver]] = {
    "mock": _mock,
    "manual": _manual,
}


@pytest.fixture(params=list(_drivers), ids=list(_drivers))
def driver(request: pytest.FixtureRequest, clock: FixedClock, settings: Settings) -> PrinterDriver:
    return _drivers[request.param](clock, settings)


# --------------------------------------------------------------- shape


def test_driver_satisfies_the_protocol(driver: PrinterDriver) -> None:
    assert isinstance(driver, PrinterDriver)
    assert driver.brand


async def test_connect_then_report_capabilities(driver: PrinterDriver) -> None:
    await driver.connect(
        ConnectionInfo(printer_id="p", mode=_mode_for(driver)),
    )
    capabilities = await driver.capabilities()
    assert capabilities.model
    assert capabilities.nozzle_diameter_mm > 0
    assert capabilities.build_volume.x.millimetres > 0


async def test_full_job_lifecycle(driver: PrinterDriver, clock: FixedClock) -> None:
    await driver.connect(ConnectionInfo(printer_id="p", mode=_mode_for(driver)))

    assert (await driver.read_telemetry()).state.accepts_job

    ref = await driver.upload(PLATE)
    handle = await driver.start(ref, PLATE.ams_mapping)
    assert handle.value

    telemetry = await driver.read_telemetry()
    assert telemetry.state is PrinterState.PRINTING
    assert telemetry.job_handle == handle.value

    await driver.pause()
    assert (await driver.read_telemetry()).state is PrinterState.PAUSED
    await driver.resume()
    assert (await driver.read_telemetry()).state is PrinterState.PRINTING

    await driver.cancel("operator stopped it")
    assert (await driver.read_telemetry()).state.accepts_job


async def test_starting_a_second_job_while_busy_is_refused(driver: PrinterDriver) -> None:
    await driver.connect(ConnectionInfo(printer_id="p", mode=_mode_for(driver)))
    ref = await driver.upload(PLATE)
    await driver.start(ref, PLATE.ams_mapping)

    with pytest.raises(DriverRejectedError):
        await driver.start(ref, PLATE.ams_mapping)


async def test_telemetry_is_timestamped_and_attributed(driver: PrinterDriver) -> None:
    await driver.connect(ConnectionInfo(printer_id="p", mode=_mode_for(driver)))
    telemetry = await driver.read_telemetry()
    assert telemetry.printer_id
    assert telemetry.observed_at.tzinfo is not None


# ------------------------------------------------- the rules V1 broke


def test_mock_driver_refuses_to_exist_in_production(
    clock: FixedClock, production_settings: Settings
) -> None:
    """ADR-0007. A simulator must be impossible to run against a real farm."""
    with pytest.raises(ConfigurationError) as excinfo:
        MockPrinterDriver(
            ConnectionInfo(printer_id="p", mode=ConnectionMode.MOCK), clock, production_settings
        )
    assert excinfo.value.code == "error.driver.mock_in_production"


async def test_unreachable_printer_raises_and_never_fabricates(
    clock: FixedClock, settings: Settings
) -> None:
    """V1 returned invented status and invented job ids here. This must raise."""
    driver = MockPrinterDriver(
        ConnectionInfo(printer_id="dead", mode=ConnectionMode.MOCK),
        clock,
        settings,
        MockBehaviour(unreachable=True),
    )
    with pytest.raises(DriverUnavailableError):
        await driver.connect(ConnectionInfo(printer_id="dead", mode=ConnectionMode.MOCK))
    with pytest.raises(DriverUnavailableError):
        await driver.read_telemetry()


async def test_commands_on_a_disconnected_printer_raise(
    clock: FixedClock, settings: Settings
) -> None:
    driver = _mock(clock, settings)
    with pytest.raises(DriverUnavailableError):
        await driver.read_telemetry()


async def test_manual_driver_reports_none_for_what_nobody_measured(
    clock: FixedClock, settings: Settings
) -> None:
    """A human-driven machine must not invent progress, layers or temperatures."""
    driver = _manual(clock, settings)
    await driver.connect(ConnectionInfo(printer_id="manual-1", mode=ConnectionMode.MANUAL))
    ref = await driver.upload(PLATE)
    await driver.start(ref, {})

    telemetry = await driver.read_telemetry()
    assert telemetry.state is PrinterState.PRINTING
    assert telemetry.progress_percent is None
    assert telemetry.layer_current is None
    assert telemetry.remaining is None
    assert telemetry.nozzle_temp_c is None


async def test_mock_progress_is_driven_by_the_clock_not_by_sleeping(
    clock: FixedClock, settings: Settings
) -> None:
    driver = MockPrinterDriver(
        ConnectionInfo(printer_id="p", mode=ConnectionMode.MOCK),
        clock,
        settings,
        MockBehaviour(print_duration=Duration.from_hours(4)),
    )
    await driver.connect(ConnectionInfo(printer_id="p", mode=ConnectionMode.MOCK))
    await driver.start(await driver.upload(PLATE), {})

    clock.advance(timedelta(hours=1))
    assert (await driver.read_telemetry()).progress_percent == 25

    clock.advance(timedelta(hours=3))
    assert (await driver.read_telemetry()).state is PrinterState.FINISHED


async def test_injected_failure_surfaces_as_error_state(
    clock: FixedClock, settings: Settings
) -> None:
    driver = MockPrinterDriver(
        ConnectionInfo(printer_id="p", mode=ConnectionMode.MOCK),
        clock,
        settings,
        MockBehaviour(fail_at_percent=50),
    )
    await driver.connect(ConnectionInfo(printer_id="p", mode=ConnectionMode.MOCK))
    await driver.start(await driver.upload(PLATE), {})

    clock.advance(timedelta(hours=1, minutes=1))
    telemetry = await driver.read_telemetry()
    assert telemetry.state is PrinterState.ERROR
    assert telemetry.error_code == "mock.injected_failure"


def test_registry_refuses_unknown_brands(clock: FixedClock, settings: Settings) -> None:
    """No silent downgrade to a simulator for an unrecognised printer."""
    with pytest.raises(ConfigurationError) as excinfo:
        build(
            "nonexistent",
            ConnectionInfo(printer_id="p", mode=ConnectionMode.LAN),
            clock,
            settings,
        )
    assert excinfo.value.code == "error.driver.unknown_brand"


def test_registry_exposes_every_registered_brand() -> None:
    """`bambu` joined in Phase 3; `manual` and `mock` have been there since Phase 0."""
    assert available() == ("bambu", "manual", "mock")


# ------------------------------------------- rules learned from real hardware


async def test_a_printer_with_no_storage_fails_upload_distinguishably(
    clock: FixedClock, settings: Settings
) -> None:
    """A real printer with no memory card connects, authenticates, lists directories
    happily, and then fails every write with a bare "553 Could not create file".

    That must not surface as a generic rejection: the remedy is physical and
    specific, and in a farm of twenty machines the operator needs to be told which
    one to walk to.
    """
    driver = MockPrinterDriver(
        ConnectionInfo(printer_id="cardless", mode=ConnectionMode.MOCK),
        clock,
        settings,
        MockBehaviour(no_storage=True),
    )
    await driver.connect(ConnectionInfo(printer_id="cardless", mode=ConnectionMode.MOCK))

    # Reachable and readable — the failure is narrowly about storage.
    assert (await driver.read_telemetry()).state is PrinterState.IDLE

    with pytest.raises(DriverStorageError) as excinfo:
        await driver.upload(PLATE)
    assert excinfo.value.code == "error.driver.storage_unavailable"
    assert excinfo.value.details["printer"] == "cardless"


def test_storage_failure_is_not_confused_with_rejection_or_unreachability() -> None:
    assert not issubclass(DriverStorageError, DriverRejectedError)
    assert not issubclass(DriverStorageError, DriverUnavailableError)
    assert issubclass(DriverStorageError, DriverError)


def test_a_finished_printer_does_not_accept_new_work() -> None:
    """The finished part is still on the bed.

    A real Bambu reports ``gcode_state: FINISH`` at 100% with the nozzle cooled to
    ambient — indistinguishable from idle in every numeric field, yet dispatching
    to it would print onto an occupied plate. Found by the Phase 0 spike.
    """
    assert not PrinterState.FINISHED.accepts_job
    assert PrinterState.IDLE.accepts_job


def test_only_idle_accepts_work() -> None:
    accepting = [state for state in PrinterState if state.accepts_job]
    assert accepting == [PrinterState.IDLE]


def test_states_needing_a_human_are_neither_busy_nor_available() -> None:
    for state in (PrinterState.FINISHED, PrinterState.ERROR, PrinterState.MAINTENANCE):
        assert state.needs_attention
        assert not state.is_busy
        assert not state.accepts_job


async def test_a_printer_that_just_finished_is_refused_a_second_job(
    clock: FixedClock, settings: Settings
) -> None:
    driver = MockPrinterDriver(
        ConnectionInfo(printer_id="p", mode=ConnectionMode.MOCK),
        clock,
        settings,
        MockBehaviour(print_duration=Duration.from_hours(1)),
    )
    await driver.connect(ConnectionInfo(printer_id="p", mode=ConnectionMode.MOCK))
    ref = await driver.upload(PLATE)
    await driver.start(ref, {})

    clock.advance(timedelta(hours=2))
    assert (await driver.read_telemetry()).state is PrinterState.FINISHED

    with pytest.raises(DriverRejectedError):
        await driver.start(ref, {})


def _mode_for(driver: PrinterDriver) -> ConnectionMode:
    return ConnectionMode.MANUAL if driver.brand == "manual" else ConnectionMode.MOCK
