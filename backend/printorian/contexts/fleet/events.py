"""Events published by the fleet context.

These are what the personnel dashboard subscribes to: a print finishing, a machine
going quiet, a service falling due. None of them carry credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from printorian.core.events import Event
from printorian.core.ids import EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class PrinterRegistered(Event):
    name: ClassVar[str] = "fleet.printer_registered"

    printer_id: EntityId
    printer_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PrinterStateChanged(Event):
    """A machine moved between states — the signal the floor reacts to."""

    name: ClassVar[str] = "fleet.printer_state_changed"

    printer_id: EntityId
    printer_name: str
    from_state: str
    to_state: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PrinterUnreachable(Event):
    """Recorded when a machine could not be reached.

    An event rather than a silent field change: a printer dropping off the network
    is something someone should be told about, not something to discover later.
    """

    name: ClassVar[str] = "fleet.printer_unreachable"

    printer_id: EntityId
    printer_name: str
    reason: str
