"""Live driver connections across scheduler and telemetry passes.

The pool exists for one reason: the workers run every few seconds, and a driver
rebuilt per pass would reconnect per pass. What is tested here is that it keeps a
connection when nothing changed, drops one when the machine's details did, and
never caches a failure — because a printer that was switched off must be able to
come back without restarting the process.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from printorian.contexts.fleet import ConnectionMode, CreatePrinter, FleetService
from printorian.contexts.fleet.models import Printer
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.secrets import SecretBox
from printorian.drivers import DriverUnavailableError
from printorian.workers.drivers import DriverPool

KEY = "a-development-secret-key-not-for-production"


class FakeDriver:
    """Counts what the pool does to it."""

    def __init__(self) -> None:
        self.connects = 0
        self.disconnects = 0

    @property
    def brand(self) -> str:
        return "fake"

    async def connect(self, info: object) -> None:
        self.connects += 1

    async def disconnect(self) -> None:
        self.disconnects += 1


@pytest.fixture
def fleet(db_session: object, clock: FixedClock, bus: EventBus) -> FleetService:
    return FleetService(db_session, clock, bus, SecretBox(KEY))  # type: ignore[arg-type]


async def a_printer(
    fleet: FleetService, *, name: str = "P1S-01", host: str = "10.0.0.5"
) -> Printer:
    view = await fleet.register(
        CreatePrinter(
            name=name,
            brand="fake",
            model="P1S",
            connection_mode=ConnectionMode.LAN,
            host=host,
            serial="SERIAL-1",
            width_mm=Decimal(256),
            depth_mm=Decimal(256),
            height_mm=Decimal(256),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=True,
        )
    )
    return await fleet._load(view.id)


def pool_with(monkeypatch: pytest.MonkeyPatch, driver: object, clock: FixedClock) -> DriverPool:
    """A pool whose registry hands back the driver under test."""
    from printorian.workers import drivers as module

    monkeypatch.setattr(module.driver_registry, "build", lambda *a, **k: driver)
    return DriverPool(clock, Settings(secret_key=KEY))


async def test_an_unchanged_printer_keeps_its_connection(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    """The whole point.

    At a thirty-second tick, reconnecting each pass is two connection attempts a
    minute per machine — the reconnect storm ARCHITECTURE names as a real failure.
    """
    printer = await a_printer(fleet)
    driver = FakeDriver()
    pool = pool_with(monkeypatch, driver, clock)

    for _ in range(5):
        drivers = await pool.refresh(fleet, [printer])

    assert driver.connects == 1
    assert driver.disconnects == 0
    assert set(drivers) == {str(printer.id)}


async def test_a_changed_host_is_reconnected(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    """A machine that moved is a different machine to reach."""
    printer = await a_printer(fleet)
    driver = FakeDriver()
    pool = pool_with(monkeypatch, driver, clock)
    await pool.refresh(fleet, [printer])

    printer.host = "10.0.0.9"
    await pool.refresh(fleet, [printer])

    assert driver.connects == 2
    # Closed before the replacement opened, or the old session stays attached.
    assert driver.disconnects == 1


async def test_a_renamed_printer_keeps_its_connection(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    """The fingerprint is what it takes to reach the machine, not what it is called."""
    printer = await a_printer(fleet)
    driver = FakeDriver()
    pool = pool_with(monkeypatch, driver, clock)
    await pool.refresh(fleet, [printer])

    printer.name = "Renamed"
    await pool.refresh(fleet, [printer])

    assert driver.connects == 1


async def test_a_printer_that_disappears_is_disconnected(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    """Otherwise the socket stays open against a machine nobody is watching."""
    printer = await a_printer(fleet)
    driver = FakeDriver()
    pool = pool_with(monkeypatch, driver, clock)
    await pool.refresh(fleet, [printer])

    drivers = await pool.refresh(fleet, [])

    assert drivers == {}
    assert driver.disconnects == 1


async def test_a_failure_is_retried_rather_than_cached(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    """A printer switched off at 09:00 must come back at 09:05 by itself."""
    printer = await a_printer(fleet)

    class Flaky(FakeDriver):
        fail = True

        async def connect(self, info: object) -> None:
            if Flaky.fail:
                raise DriverUnavailableError("error.driver.unavailable")
            await super().connect(info)

    driver = Flaky()
    pool = pool_with(monkeypatch, driver, clock)

    assert await pool.refresh(fleet, [printer]) == {}

    Flaky.fail = False
    drivers = await pool.refresh(fleet, [printer])

    assert set(drivers) == {str(printer.id)}
    assert driver.connects == 1


async def test_closing_the_pool_disconnects_everything(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    printer = await a_printer(fleet)
    driver = FakeDriver()
    pool = pool_with(monkeypatch, driver, clock)
    await pool.refresh(fleet, [printer])

    await pool.aclose()

    assert driver.disconnects == 1
    assert await pool.refresh(fleet, []) == {}


async def test_a_broken_disconnect_does_not_fail_the_pass(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    """The socket is going away either way; a raise here would end the loop."""
    printer = await a_printer(fleet)

    class Rude(FakeDriver):
        async def disconnect(self) -> None:
            raise OSError("connection already gone")

    pool = pool_with(monkeypatch, Rude(), clock)
    await pool.refresh(fleet, [printer])

    assert await pool.refresh(fleet, []) == {}


async def test_a_printer_that_stays_off_is_reported_once(
    fleet: FleetService,
    monkeypatch: pytest.MonkeyPatch,
    clock: FixedClock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Six offline machines on a thirty-second tick is 720 lines an hour.

    A log nobody reads is the same as no log, so an unchanged failure is silent
    after the first — while a *change* of reason still gets through.
    """
    printer = await a_printer(fleet)

    class Dead(FakeDriver):
        async def connect(self, info: object) -> None:
            raise DriverUnavailableError("error.driver.unavailable")

    pool = pool_with(monkeypatch, Dead(), clock)
    for _ in range(5):
        await pool.refresh(fleet, [printer])

    assert capsys.readouterr().out.count("driver_unavailable") == 1


async def test_a_recovery_is_always_reported(
    fleet: FleetService,
    monkeypatch: pytest.MonkeyPatch,
    clock: FixedClock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A connection coming back is news, and repeated flapping is a fault to see."""
    printer = await a_printer(fleet)

    class Flaky(FakeDriver):
        fail = True

        async def connect(self, info: object) -> None:
            if Flaky.fail:
                raise DriverUnavailableError("error.driver.unavailable")
            await super().connect(info)

    pool = pool_with(monkeypatch, Flaky(), clock)
    await pool.refresh(fleet, [printer])
    Flaky.fail = False
    await pool.refresh(fleet, [printer])

    printed = capsys.readouterr().out
    assert "driver_connected" in printed
    # Names what it recovered from, so a flapping machine is visible as flapping.
    assert "recovered=error.driver.unavailable" in printed
