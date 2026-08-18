"""The virtual farm.

A deterministic, in-memory fleet of mock printers. Time advances only when the
test says so, so a farm-day runs in milliseconds and never flakes.

This exists in Phase 0, before the features it will test, for two reasons
(ROADMAP, "Why the virtual farm is Phase 0"):

* Phases 4–5 develop the scheduler and the production pipeline against it, without
  occupying real printers or waiting for real prints.
* It is the mechanism that makes V1's failure impossible to repeat. A fabricating
  driver cannot pass a harness that asserts on observed state transitions.

As later phases land, the farm gains the scheduler and the job pipeline. In Phase 0
it drives drivers directly and proves the loop closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import Event, EventBus
from printorian.core.units import Duration
from printorian.drivers import (
    ConnectionInfo,
    ConnectionMode,
    DriverError,
    JobHandle,
    MockBehaviour,
    MockPrinterDriver,
    PlateUpload,
    PrinterState,
    Telemetry,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TelemetryObserved(Event):
    """Published for every observation the poller takes.

    Phase 3 replaces this with the fleet context's own event; the shape is the same
    so the harness keeps working.
    """

    name = "fleet.telemetry_observed"  # type: ignore[misc]

    printer_id: str
    state: PrinterState
    progress_percent: int | None = None
    error_code: str | None = None


@dataclass(slots=True)
class PrinterSpec:
    """How one virtual machine should behave."""

    printer_id: str
    behaviour: MockBehaviour = field(default_factory=MockBehaviour)


class VirtualFarm:
    """A fleet of mock printers sharing one clock and one event bus."""

    def __init__(
        self,
        clock: FixedClock,
        settings: Settings,
        bus: EventBus,
        specs: list[PrinterSpec],
    ) -> None:
        self._clock = clock
        self._bus = bus
        self._drivers: dict[str, MockPrinterDriver] = {
            spec.printer_id: MockPrinterDriver(
                ConnectionInfo(printer_id=spec.printer_id, mode=ConnectionMode.MOCK),
                clock,
                settings,
                spec.behaviour,
            )
            for spec in specs
        }
        #: Printers whose driver raised on the last poll — offline, never invented.
        self.unreachable: set[str] = set()

    @classmethod
    def of_size(
        cls,
        size: int,
        clock: FixedClock,
        settings: Settings,
        bus: EventBus,
        behaviour: MockBehaviour | None = None,
    ) -> VirtualFarm:
        specs = [
            PrinterSpec(
                printer_id=f"vp-{i:02d}",
                behaviour=behaviour or MockBehaviour(),
            )
            for i in range(size)
        ]
        return cls(clock, settings, bus, specs)

    @property
    def printer_ids(self) -> list[str]:
        return sorted(self._drivers)

    def driver(self, printer_id: str) -> MockPrinterDriver:
        return self._drivers[printer_id]

    # -- lifecycle -------------------------------------------------------

    async def connect_all(self) -> None:
        for printer_id, driver in self._drivers.items():
            try:
                await driver.connect(
                    ConnectionInfo(printer_id=printer_id, mode=ConnectionMode.MOCK)
                )
            except DriverError:
                self.unreachable.add(printer_id)

    async def dispatch(self, printer_id: str, plate: PlateUpload) -> JobHandle:
        driver = self._drivers[printer_id]
        ref = await driver.upload(plate)
        return await driver.start(ref, plate.ams_mapping)

    async def idle_printers(self) -> list[str]:
        """Printers ready to accept work, established by asking them."""
        ready: list[str] = []
        for printer_id, driver in self._drivers.items():
            try:
                if (await driver.read_telemetry()).state.accepts_job:
                    ready.append(printer_id)
            except DriverError:
                self.unreachable.add(printer_id)
        return sorted(ready)

    # -- observation -----------------------------------------------------

    async def poll(self) -> list[Telemetry]:
        """Take one observation per printer and publish it.

        A driver that raises marks its printer unreachable. Nothing is substituted:
        an unreachable printer contributes no telemetry at all.
        """
        observations: list[Telemetry] = []
        for printer_id, driver in self._drivers.items():
            try:
                telemetry = await driver.read_telemetry()
            except DriverError:
                self.unreachable.add(printer_id)
                continue

            self.unreachable.discard(printer_id)
            observations.append(telemetry)
            await self._bus.publish(
                TelemetryObserved(
                    printer_id=telemetry.printer_id,
                    state=telemetry.state,
                    progress_percent=telemetry.progress_percent,
                    error_code=telemetry.error_code,
                )
            )
        return observations

    async def advance(self, delta: timedelta) -> list[Telemetry]:
        """Move time forward, then observe."""
        self._clock.advance(delta)
        return await self.poll()

    async def run_until_settled(
        self, step: timedelta = timedelta(minutes=15), max_steps: int = 200
    ) -> list[Telemetry]:
        """Advance until no printer is busy, or ``max_steps`` is exhausted."""
        observations: list[Telemetry] = []
        for _ in range(max_steps):
            observations = await self.advance(step)
            if not any(t.state.is_busy for t in observations):
                return observations
        raise AssertionError("virtual farm did not settle — a print never terminated")


def plate(name: str = "plate.3mf", slot: int = 0) -> PlateUpload:
    return PlateUpload(filename=name, content=b"virtual-plate", ams_mapping={0: slot})


def two_hour_print() -> MockBehaviour:
    return MockBehaviour(print_duration=Duration.from_hours(2), layer_total=400)
