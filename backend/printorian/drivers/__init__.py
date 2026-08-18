"""Printer protocol adapters.

Depends on :mod:`printorian.core` only — never on a business context (ADR-0011).

Available in Phase 0: ``mock`` (virtual farm, tests) and ``manual`` (human-driven
machines). ``bambu`` lands in Phase 3, ``elegoo`` in Phase 7.
"""

from printorian.drivers.base import (
    AmsSlot,
    Capabilities,
    ConnectionInfo,
    ConnectionMode,
    DriverAuthError,
    DriverError,
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
from printorian.drivers.manual import ManualPrinterDriver
from printorian.drivers.mock import MockBehaviour, MockPrinterDriver
from printorian.drivers.registry import available, build, register

__all__ = [
    "AmsSlot",
    "Capabilities",
    "ConnectionInfo",
    "ConnectionMode",
    "DriverAuthError",
    "DriverError",
    "DriverRejectedError",
    "DriverStorageError",
    "DriverUnavailableError",
    "JobHandle",
    "ManualPrinterDriver",
    "MockBehaviour",
    "MockPrinterDriver",
    "PlateUpload",
    "PrinterDriver",
    "PrinterState",
    "RemoteFileRef",
    "Telemetry",
    "available",
    "build",
    "register",
]
