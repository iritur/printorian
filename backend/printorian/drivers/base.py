"""The printer driver contract.

Every brand implements this interface and nothing wider. ``scheduling`` and
``production`` talk to printers only through it, which is what keeps the fleet
brand-neutral (ADR-0011).

**The rule that exists because of V1**: a driver must never invent data. If the
printer cannot be reached, raise :class:`DriverUnavailableError` — the fleet
context turns that into an ``Offline`` state and an alert. V1's Bambu connector
silently returned fabricated status and fabricated job ids on every failure, so a
non-functional integration sat at the centre of the product undetected. There is
no fallback path in this interface, by design.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from printorian.core.errors import IntegrationError
from printorian.core.units import BoundingBox, Duration


class DriverError(IntegrationError):
    """Base for driver failures."""

    code = "error.driver"


class DriverUnavailableError(DriverError):
    """The printer could not be reached or did not answer in time."""

    code = "error.driver.unavailable"


class DriverAuthError(DriverError):
    """Credentials rejected — wrong access code, serial, or expired token."""

    code = "error.driver.auth"


class DriverRejectedError(DriverError):
    """The printer understood the command and refused it."""

    code = "error.driver.rejected"


class DriverStorageError(DriverError):
    """The printer is reachable and authenticated, but cannot store a plate.

    Distinct from :class:`DriverRejectedError` because the remedy is physical and
    specific: usually no memory card is mounted, or it is full or write-protected.

    Found during the Phase 0 spike, where a real printer with no card accepted the
    connection, authenticated, served directory listings, and then failed every
    write with a bare ``553 Could not create file`` — a message that reads like a
    permissions fault. In a farm of twenty machines that has to surface as
    "printer 7 has no usable storage", not as a mystery at dispatch time.

    A printer in this condition must not be treated as available capacity.
    """

    code = "error.driver.storage_unavailable"


class PrinterState(StrEnum):
    """Coarse machine state, normalized across brands."""

    OFFLINE = "offline"
    IDLE = "idle"
    PREPARING = "preparing"
    PRINTING = "printing"
    PAUSED = "paused"
    FINISHED = "finished"
    ERROR = "error"
    MAINTENANCE = "maintenance"

    @property
    def is_busy(self) -> bool:
        return self in {PrinterState.PREPARING, PrinterState.PRINTING, PrinterState.PAUSED}

    @property
    def accepts_job(self) -> bool:
        """Only an empty, idle machine can take work.

        ``FINISHED`` is deliberately excluded. A real Bambu reports
        ``gcode_state: FINISH`` at 100% with the finished part **still on the bed**
        and the nozzle cooled — it looks idle in every numeric field, but
        dispatching to it would print onto an occupied plate.

        This was found by pointing the Phase 0 spike at a real printer, which
        reported exactly that state. ``FINISHED`` becomes ``IDLE`` only when a human
        clears the plate — the scenario's step 10, where personnel are alerted to
        collect the finished part.
        """
        return self is PrinterState.IDLE

    @property
    def needs_attention(self) -> bool:
        """Not printing, but not available either — a human has to do something."""
        return self in {PrinterState.FINISHED, PrinterState.ERROR, PrinterState.MAINTENANCE}


class ConnectionMode(StrEnum):
    """How a printer is reached.

    ``MANUAL`` is a first-class citizen, not a degraded state: it is how machines
    without a driver (Elegoo today) participate in the fleet, with humans advancing
    their state. It is honest about being manual — unlike a silent simulation.
    """

    LAN = "lan"
    CLOUD = "cloud"
    MANUAL = "manual"
    MOCK = "mock"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectionInfo:
    """Everything needed to reach one printer."""

    printer_id: str
    mode: ConnectionMode
    host: str | None = None
    serial: str | None = None
    access_code: str | None = None
    timeout_seconds: int = 10


@dataclass(frozen=True, slots=True, kw_only=True)
class AmsSlot:
    """One material slot. ``index`` is 0-based within ``unit``."""

    unit: int
    index: int
    material_type: str | None = None
    colour_hex: str | None = None
    remaining_percent: int | None = None

    @property
    def is_loaded(self) -> bool:
        return self.material_type is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """What a printer can physically do — the hard constraints the scheduler filters on."""

    model: str
    build_volume: BoundingBox
    nozzle_diameter_mm: Decimal
    supports_multi_material: bool = False
    ams_slots: tuple[AmsSlot, ...] = ()
    max_nozzle_temp_c: int = 300
    max_bed_temp_c: int = 100
    #: Nominal draw used for electricity costing until measured values exist.
    nominal_power_kw: Decimal = Decimal("0.35")


@dataclass(frozen=True, slots=True, kw_only=True)
class Telemetry:
    """A single observation. Always sourced from the machine — never synthesized."""

    printer_id: str
    observed_at: datetime
    state: PrinterState
    job_handle: str | None = None
    progress_percent: int | None = None
    layer_current: int | None = None
    layer_total: int | None = None
    remaining: Duration | None = None
    nozzle_temp_c: Decimal | None = None
    bed_temp_c: Decimal | None = None
    ams_slots: tuple[AmsSlot, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PlateUpload:
    """A sliced plate on its way to a printer."""

    filename: str
    content: bytes
    #: Slot index per filament used by the plate, resolved by the scheduler.
    ams_mapping: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteFileRef:
    """Where the plate landed on the printer."""

    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class JobHandle:
    """The printer's own identifier for a running job."""

    value: str


@runtime_checkable
class PrinterDriver(Protocol):
    """Protocol every brand adapter implements."""

    @property
    def brand(self) -> str: ...

    async def connect(self, info: ConnectionInfo) -> None:
        """Establish and verify a connection. Raise if it cannot be established."""
        ...

    async def disconnect(self) -> None: ...

    async def capabilities(self) -> Capabilities:
        """Report physical capabilities, read from the machine where possible."""
        ...

    async def read_telemetry(self) -> Telemetry:
        """One observation. Raise :class:`DriverUnavailableError` if unreachable."""
        ...

    def stream_telemetry(self) -> AsyncIterator[Telemetry]:
        """Continuous observations for the poller to fan out onto the event bus."""
        ...

    async def upload(self, plate: PlateUpload) -> RemoteFileRef: ...

    async def start(self, ref: RemoteFileRef, ams_mapping: dict[int, int]) -> JobHandle: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def cancel(self, reason: str) -> None: ...
