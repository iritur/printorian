"""Manual driver — machines a human drives.

This is how printers without a protocol adapter (Elegoo, today) take part in the
fleet: they are real, schedulable machines whose state is advanced by an operator
in the UI rather than over the wire.

It is deliberately *not* a simulator. It never reports progress it did not observe;
it reports exactly what a human last told it, and ``None`` for everything a human
cannot know. An operator-driven printer is honest about being operator-driven.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from printorian.core.clock import Clock
from printorian.core.errors import ConfigurationError
from printorian.core.units import BoundingBox, Length
from printorian.drivers.base import (
    Capabilities,
    ConnectionInfo,
    ConnectionMode,
    DriverRejectedError,
    JobHandle,
    PlateUpload,
    PrinterDriver,
    PrinterState,
    RemoteFileRef,
    Telemetry,
)


class ManualPrinterDriver(PrinterDriver):
    """State is set by people; the driver only records and reports it."""

    def __init__(
        self,
        info: ConnectionInfo,
        clock: Clock,
        capabilities: Capabilities | None = None,
    ) -> None:
        if info.mode is not ConnectionMode.MANUAL:
            raise ConfigurationError(
                "error.driver.manual_requires_manual_mode", mode=info.mode.value
            )
        self._info = info
        self._clock = clock
        self._capabilities = capabilities or Capabilities(
            model="Manual machine",
            build_volume=BoundingBox(x=Length(220), y=Length(220), z=Length(250)),
            nozzle_diameter_mm=Decimal("0.4"),
        )
        self._state = PrinterState.IDLE
        self._handle: JobHandle | None = None

    @property
    def brand(self) -> str:
        return "manual"

    async def connect(self, info: ConnectionInfo) -> None:
        self._info = info

    async def disconnect(self) -> None:
        return None

    async def capabilities(self) -> Capabilities:
        return self._capabilities

    async def read_telemetry(self) -> Telemetry:
        """Report the last human-declared state, and nothing more.

        Progress, layers, remaining time and temperatures stay ``None``: nobody
        measured them, so the system does not claim them.
        """
        return Telemetry(
            printer_id=self._info.printer_id,
            observed_at=self._clock.now(),
            state=self._state,
            job_handle=self._handle.value if self._handle else None,
        )

    async def stream_telemetry(self) -> AsyncIterator[Telemetry]:
        yield await self.read_telemetry()

    async def upload(self, plate: PlateUpload) -> RemoteFileRef:
        """Nothing is transferred; the operator carries the file to the machine."""
        return RemoteFileRef(path=f"manual://{plate.filename}")

    async def start(self, ref: RemoteFileRef, ams_mapping: dict[int, int]) -> JobHandle:
        if not self._state.accepts_job:
            raise DriverRejectedError("error.driver.busy", state=self._state.value)
        self._state = PrinterState.PRINTING
        self._handle = JobHandle(value=f"manual-{ref.path.rsplit('/', 1)[-1]}")
        return self._handle

    async def pause(self) -> None:
        if self._state is PrinterState.PRINTING:
            self._state = PrinterState.PAUSED

    async def resume(self) -> None:
        if self._state is PrinterState.PAUSED:
            self._state = PrinterState.PRINTING

    async def cancel(self, reason: str) -> None:
        self._state = PrinterState.IDLE
        self._handle = None

    # -- the human-facing half -------------------------------------------

    def declare_state(self, state: PrinterState) -> None:
        """Record what an operator says the machine is doing."""
        self._state = state
        if not state.is_busy:
            self._handle = None
