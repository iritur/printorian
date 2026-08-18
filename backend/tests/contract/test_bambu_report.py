"""Parsing Bambu reports, against what a real machine actually sent.

The fixture below is the payload observed during the Phase 0 spike (X2D / PF004-P,
LAN mode) — not an invented example. Every assertion that looks fussy here is one
the hardware corrected: `FINISH` is not idle, colours are RGBA, and `subtask_name`
is a profile rather than a filename.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from printorian.drivers.bambu import normalize_colour, parse_ams, parse_report, parse_state
from printorian.drivers.base import PrinterState

OBSERVED_AT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

#: Shape and values as reported by the bench printer after a completed job.
REAL_REPORT = {
    "print": {
        "gcode_state": "FINISH",
        "mc_percent": 100,
        "layer_num": 785,
        "total_layer_num": 785,
        "mc_remaining_time": 0,
        "nozzle_temper": 28.0,
        "bed_temper": 26.0,
        "subtask_name": "0.2mm layer, 2 walls, 15% infill",
        "ams": {
            "ams": [
                {
                    "id": "0",
                    "tray": [
                        {"id": "0"},
                        {"id": "1"},
                        {"id": "2"},
                        {"id": "3", "tray_type": "PLA", "tray_color": "FFFFFFFF", "remain": 80},
                    ],
                }
            ]
        },
    }
}


def test_the_observed_report_parses() -> None:
    telemetry = parse_report(REAL_REPORT, printer_id="x2d-01", observed_at=OBSERVED_AT)

    assert telemetry is not None
    assert telemetry.progress_percent == 100
    assert telemetry.layer_current == 785
    assert telemetry.layer_total == 785
    assert telemetry.nozzle_temp_c == Decimal("28.0")
    assert telemetry.observed_at is OBSERVED_AT


def test_finish_is_not_idle() -> None:
    """100%, cooled nozzle, and the part still on the bed.

    Reading this as available capacity would dispatch onto an occupied plate.
    """
    telemetry = parse_report(REAL_REPORT, printer_id="x2d-01", observed_at=OBSERVED_AT)

    assert telemetry is not None
    assert telemetry.state is PrinterState.FINISHED
    assert not telemetry.state.accepts_job


def test_ams_colour_is_rgba_not_hex() -> None:
    slots = parse_ams(REAL_REPORT["print"])
    loaded = [slot for slot in slots if slot.is_loaded]

    assert len(loaded) == 1
    assert loaded[0].unit == 0
    assert loaded[0].index == 3
    assert loaded[0].material_type == "PLA"
    # FFFFFFFF is white with full alpha; the alpha is a protocol detail, not colour.
    assert loaded[0].colour_hex == "#FFFFFF"
    assert loaded[0].remaining_percent == 80


def test_empty_slots_are_reported_but_not_loaded() -> None:
    slots = parse_ams(REAL_REPORT["print"])
    assert len(slots) == 4
    assert sum(1 for slot in slots if not slot.is_loaded) == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FFFFFFFF", "#FFFFFF"),
        ("ff0000ff", "#FF0000"),
        ("#00FF00", "#00FF00"),
        ("", None),
        (None, None),
        ("nonsense", None),
    ],
)
def test_colour_normalisation(raw: str | None, expected: str | None) -> None:
    assert normalize_colour(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IDLE", PrinterState.IDLE),
        ("RUNNING", PrinterState.PRINTING),
        ("PAUSE", PrinterState.PAUSED),
        ("FINISH", PrinterState.FINISHED),
        ("FAILED", PrinterState.ERROR),
        ("PREPARE", PrinterState.PREPARING),
    ],
)
def test_known_states_map(raw: str, expected: PrinterState) -> None:
    assert parse_state(raw) is expected


def test_an_unknown_state_is_offline_never_idle() -> None:
    """Offering work on the strength of an unrecognised string is how fleets
    dispatch onto machines nobody has verified."""
    assert parse_state("SOMETHING_NEW") is PrinterState.OFFLINE
    assert parse_state(None) is PrinterState.OFFLINE
    assert not parse_state("SOMETHING_NEW").accepts_job


def test_non_print_messages_are_not_observations() -> None:
    """The report topic also carries `system` messages; they are not telemetry."""
    assert (
        parse_report(
            {"system": {"command": "get_access_code"}}, printer_id="x2d-01", observed_at=OBSERVED_AT
        )
        is None
    )
    assert parse_report({}, printer_id="x2d-01", observed_at=OBSERVED_AT) is None


def test_a_missing_field_does_not_discard_the_observation() -> None:
    """Reports arrive partial; a missing layer count is not a reason to drop one."""
    sparse = {"print": {"gcode_state": "RUNNING", "mc_percent": 42}}
    telemetry = parse_report(sparse, printer_id="x2d-01", observed_at=OBSERVED_AT)

    assert telemetry is not None
    assert telemetry.state is PrinterState.PRINTING
    assert telemetry.progress_percent == 42
    assert telemetry.layer_total is None
    assert telemetry.remaining is None


def test_print_errors_surface_with_a_code() -> None:
    failed = {"print": {"gcode_state": "FAILED", "print_error": 83935249}}
    telemetry = parse_report(failed, printer_id="x2d-01", observed_at=OBSERVED_AT)

    assert telemetry is not None
    assert telemetry.state is PrinterState.ERROR
    assert telemetry.error_code == "bambu.print_error.83935249"


def test_no_error_code_when_the_machine_reports_none() -> None:
    telemetry = parse_report(REAL_REPORT, printer_id="x2d-01", observed_at=OBSERVED_AT)
    assert telemetry is not None
    assert telemetry.error_code is None


def test_remaining_time_becomes_a_duration() -> None:
    running = {"print": {"gcode_state": "RUNNING", "mc_remaining_time": 42}}
    telemetry = parse_report(running, printer_id="x2d-01", observed_at=OBSERVED_AT)

    assert telemetry is not None
    assert telemetry.remaining is not None
    assert telemetry.remaining.minutes == Decimal(42)
