"""Parsing Bambu's MQTT report payload.

Split from the transport so it can be tested against recorded fixtures without a
socket — the whole point of the Phase 0 spike was to capture what a real machine
actually sends (docs/BAMBU-LAN-PROTOCOL.md).

Three details here were corrected by real hardware rather than guessed:

* ``FINISH`` is **not** idle. The observed machine reported 100%, 785/785 layers and
  a cooled nozzle while the finished part was still on the bed. Treating that as
  available capacity would dispatch a job onto an occupied plate.
* ``tray_color`` is 8-character **RGBA** (``FFFFFFFF``), not ``#RRGGBB``.
* ``subtask_name`` is the print *profile* ("0.2mm layer, 2 walls, 15% infill"), not a
  filename, so it cannot correlate a running print back to a job.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from printorian.core.units import Duration
from printorian.drivers.base import AmsSlot, PrinterState, Telemetry

#: Bambu's vocabulary mapped onto ours.
GCODE_STATE: dict[str, PrinterState] = {
    "IDLE": PrinterState.IDLE,
    "READY": PrinterState.IDLE,
    "PREPARE": PrinterState.PREPARING,
    "SLICING": PrinterState.PREPARING,
    "RUNNING": PrinterState.PRINTING,
    "PAUSE": PrinterState.PAUSED,
    "FINISH": PrinterState.FINISHED,
    "FAILED": PrinterState.ERROR,
}


def parse_state(raw: str | None) -> PrinterState:
    """Map a reported gcode_state, defaulting to OFFLINE for anything unknown.

    An unrecognised state must never fall through to IDLE: that would offer the
    machine work on the strength of a string nobody has seen before.
    """
    if not raw:
        return PrinterState.OFFLINE
    return GCODE_STATE.get(raw.upper(), PrinterState.OFFLINE)


def normalize_colour(raw: str | None) -> str | None:
    """Turn Bambu's 8-char RGBA into a CSS hex colour.

    Alpha is dropped: it describes the report format, not the filament, and keeping
    it would leak a protocol detail into every colour swatch in the UI.
    """
    if not raw:
        return None
    value = raw.strip().lstrip("#")
    if len(value) == _RGBA_LENGTH:
        value = value[:_RGB_LENGTH]
    if len(value) != _RGB_LENGTH:
        return None
    # Length alone is not enough: an 8-character non-hex string would truncate to
    # six characters and produce something like "#NONSEN", which is not a colour
    # and would reach a CSS swatch looking plausible.
    if any(char not in _HEX_DIGITS for char in value.upper()):
        return None
    return f"#{value.upper()}"


def parse_ams(payload: dict[str, Any]) -> tuple[AmsSlot, ...]:
    """Extract every loaded slot from a report."""
    ams = payload.get("ams")
    if not isinstance(ams, dict):
        return ()

    slots: list[AmsSlot] = []
    for unit in ams.get("ams", []) or []:
        if not isinstance(unit, dict):
            continue
        unit_id = _as_index(unit.get("id"))
        for tray in unit.get("tray", []) or []:
            if not isinstance(tray, dict):
                continue
            slots.append(
                AmsSlot(
                    unit=unit_id,
                    index=_as_index(tray.get("id")),
                    material_type=(tray.get("tray_type") or None),
                    colour_hex=normalize_colour(tray.get("tray_color")),
                    remaining_percent=_as_int(tray.get("remain"), default=None),
                )
            )
    return tuple(slots)


def parse_report(
    payload: dict[str, Any], *, printer_id: str, observed_at: datetime
) -> Telemetry | None:
    """Turn one MQTT report into telemetry, or ``None`` if it carries no state.

    Bambu interleaves other message kinds on the same topic (``system``, and others);
    those are not observations and must not be mistaken for one.
    """
    printer = payload.get("print")
    if not isinstance(printer, dict):
        return None

    state = parse_state(printer.get("gcode_state"))
    remaining = _as_int(printer.get("mc_remaining_time"), default=None)

    return Telemetry(
        printer_id=printer_id,
        observed_at=observed_at,
        state=state,
        job_handle=printer.get("subtask_id") or None,
        progress_percent=_as_int(printer.get("mc_percent"), default=None),
        layer_current=_as_int(printer.get("layer_num"), default=None),
        layer_total=_as_int(printer.get("total_layer_num"), default=None),
        remaining=Duration(Decimal(remaining)) if remaining is not None else None,
        nozzle_temp_c=_as_decimal(printer.get("nozzle_temper")),
        bed_temp_c=_as_decimal(printer.get("bed_temper")),
        ams_slots=parse_ams(printer),
        error_code=_error_code(printer),
    )


def _error_code(printer: dict[str, Any]) -> str | None:
    code = printer.get("print_error")
    if code in (None, 0, "0"):
        return None
    return f"bambu.print_error.{code}"


def _as_int(value: object, *, default: int | None) -> int | None:
    """Coerce a reported value, falling back rather than raising.

    Reports occasionally carry nulls and strings where numbers are expected; a
    missing layer count is not a reason to discard an otherwise good observation.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_index(value: object) -> int:
    """Slot and unit positions, which are structural and always present."""
    return _as_int(value, default=0) or 0


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


_RGBA_LENGTH = 8
_RGB_LENGTH = 6
_HEX_DIGITS = frozenset("0123456789ABCDEF")
