"""In-process virtual printer, for tests and the virtual farm.

Deterministic by construction: progress is derived from an injected
:class:`~printorian.core.clock.Clock`, so a full print completes the instant the
harness advances time. No sleeping, no wall-clock flakiness.

**This module refuses to load in production.** Building a mock driver while
``environment == production`` raises :class:`ConfigurationError`. That is the
structural guard against V1's failure mode, where simulated data was substituted
for real data silently and nobody noticed the integration was dead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.errors import ConfigurationError
from printorian.core.units import BoundingBox, Duration, Length
from printorian.drivers.base import (
    AmsSlot,
    Capabilities,
    ConnectionInfo,
    ConnectionMode,
    DriverRejectedError,
    DriverStorageError,
    DriverUnavailableError,
    JobHandle,
    PlateUpload,
    PrinterDriver,
    PrinterState,
    RemoteFileRef,
    Telemetry,
)

_PERCENT = 100


@dataclass(slots=True)
class MockBehaviour:
    """Knobs for exercising the unhappy paths the real farm will hit."""

    print_duration: Duration = field(default_factory=lambda: Duration.from_hours(2))
    layer_total: int = 400
    #: Progress at which the print fails, or ``None`` for a clean run.
    fail_at_percent: int | None = None
    #: When true, every call raises DriverUnavailableError, as an offline machine does.
    unreachable: bool = False
    #: Refuse ``start`` — models a printer that is reachable but rejects the job.
    reject_jobs: bool = False
    #: No writable storage: reachable and authenticated, but uploads fail.
    #: Models a real printer found during the Phase 0 spike with no memory card.
    no_storage: bool = False


class MockPrinterDriver(PrinterDriver):
    """A virtual printer that behaves like the contract says a printer behaves."""

    def __init__(
        self,
        info: ConnectionInfo,
        clock: Clock,
        settings: Settings,
        behaviour: MockBehaviour | None = None,
        capabilities: Capabilities | None = None,
    ) -> None:
        if settings.is_production:
            raise ConfigurationError(
                "error.driver.mock_in_production",
                hint="The mock driver exists for tests and the virtual farm only.",
            )
        if info.mode is not ConnectionMode.MOCK:
            raise ConfigurationError(
                "error.driver.mock_requires_mock_mode",
                mode=info.mode.value,
            )

        self._info = info
        self._clock = clock
        self._behaviour = behaviour or MockBehaviour()
        self._capabilities = capabilities or _default_capabilities()

        self._connected = False
        self._state = PrinterState.OFFLINE
        self._started_at: datetime | None = None
        self._handle: JobHandle | None = None
        self._uploaded: RemoteFileRef | None = None
        self._error_code: str | None = None

    @property
    def brand(self) -> str:
        return "mock"

    # -- connection ------------------------------------------------------

    async def connect(self, info: ConnectionInfo) -> None:
        self._guard_reachable()
        self._info = info
        self._connected = True
        self._state = PrinterState.IDLE

    async def disconnect(self) -> None:
        self._connected = False
        self._state = PrinterState.OFFLINE

    async def capabilities(self) -> Capabilities:
        self._guard_connected()
        return self._capabilities

    # -- observation -----------------------------------------------------

    async def read_telemetry(self) -> Telemetry:
        self._guard_connected()
        self._advance()

        progress = self._progress_percent()
        return Telemetry(
            printer_id=self._info.printer_id,
            observed_at=self._clock.now(),
            state=self._state,
            job_handle=self._handle.value if self._handle else None,
            progress_percent=progress,
            layer_current=(
                None if progress is None else progress * self._behaviour.layer_total // _PERCENT
            ),
            layer_total=self._behaviour.layer_total if self._state.is_busy else None,
            remaining=self._remaining(),
            nozzle_temp_c=Decimal(220) if self._state.is_busy else Decimal(25),
            bed_temp_c=Decimal(60) if self._state.is_busy else Decimal(25),
            ams_slots=self._capabilities.ams_slots,
            error_code=self._error_code,
        )

    async def stream_telemetry(self) -> AsyncIterator[Telemetry]:
        while self._connected:
            yield await self.read_telemetry()

    # -- commands --------------------------------------------------------

    async def upload(self, plate: PlateUpload) -> RemoteFileRef:
        self._guard_connected()
        if self._behaviour.no_storage:
            raise DriverStorageError(
                "error.driver.storage_unavailable", printer=self._info.printer_id
            )
        self._uploaded = RemoteFileRef(path=f"/cache/{plate.filename}")
        return self._uploaded

    async def start(self, ref: RemoteFileRef, ams_mapping: dict[int, int]) -> JobHandle:
        self._guard_connected()
        if self._behaviour.reject_jobs:
            raise DriverRejectedError("error.driver.rejected", printer=self._info.printer_id)
        if not self._state.accepts_job:
            raise DriverRejectedError("error.driver.busy", state=self._state.value)

        self._started_at = self._clock.now()
        self._state = PrinterState.PRINTING
        self._error_code = None
        self._handle = JobHandle(value=f"mock-{ref.path.rsplit('/', 1)[-1]}")
        return self._handle

    async def pause(self) -> None:
        self._guard_connected()
        if self._state is PrinterState.PRINTING:
            self._state = PrinterState.PAUSED

    async def resume(self) -> None:
        self._guard_connected()
        if self._state is PrinterState.PAUSED:
            self._state = PrinterState.PRINTING

    async def cancel(self, reason: str) -> None:
        self._guard_connected()
        self._state = PrinterState.IDLE
        self._started_at = None
        self._handle = None

    # -- internals -------------------------------------------------------

    def _guard_reachable(self) -> None:
        if self._behaviour.unreachable:
            raise DriverUnavailableError("error.driver.unavailable", printer=self._info.printer_id)

    def _guard_connected(self) -> None:
        self._guard_reachable()
        if not self._connected:
            raise DriverUnavailableError(
                "error.driver.not_connected", printer=self._info.printer_id
            )

    def _elapsed_fraction(self) -> Decimal | None:
        if self._started_at is None:
            return None
        elapsed_minutes = Decimal((self._clock.now() - self._started_at).total_seconds()) / 60
        total = self._behaviour.print_duration.minutes
        if total <= 0:
            return Decimal(1)
        return min(Decimal(1), elapsed_minutes / total)

    def _progress_percent(self) -> int | None:
        fraction = self._elapsed_fraction()
        if fraction is None or self._state in {PrinterState.IDLE, PrinterState.OFFLINE}:
            return None
        return int(fraction * _PERCENT)

    def _remaining(self) -> Duration | None:
        fraction = self._elapsed_fraction()
        if fraction is None or not self._state.is_busy:
            return None
        return Duration(self._behaviour.print_duration.minutes * (Decimal(1) - fraction))

    def _advance(self) -> None:
        """Move the state machine forward to match elapsed time."""
        if self._state is not PrinterState.PRINTING:
            return

        fraction = self._elapsed_fraction()
        if fraction is None:
            return
        progress = int(fraction * _PERCENT)

        fail_at = self._behaviour.fail_at_percent
        if fail_at is not None and progress >= fail_at:
            self._state = PrinterState.ERROR
            self._error_code = "mock.injected_failure"
            return

        if fraction >= 1:
            self._state = PrinterState.FINISHED


def _default_capabilities() -> Capabilities:
    """An X1C-shaped virtual machine: 256³ build volume, 0.4 nozzle, 4 AMS slots."""
    return Capabilities(
        model="MockCore X1",
        build_volume=BoundingBox(x=Length(256), y=Length(256), z=Length(256)),
        nozzle_diameter_mm=Decimal("0.4"),
        supports_multi_material=True,
        ams_slots=tuple(
            AmsSlot(
                unit=0,
                index=i,
                material_type="PLA",
                colour_hex="#101010",
                remaining_percent=80,
            )
            for i in range(4)
        ),
    )
