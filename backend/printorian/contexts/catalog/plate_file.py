"""Reading what a slicer produced.

Ported from the desktop app's `main/plate.ts` when ADR-0016 removed it. The
engineer now slices locally and **uploads** the plate, so the file arrives here
instead of being noticed in a watched folder — but what has to be read out of it,
and the reason to be careful about it, are unchanged.

Print minutes and per-slot grams are the *truth* an order is repriced against
(ADR-0013), so a wrong parse is worse than no parse. Every function returns
``None`` — or an empty mapping — rather than a guess when the file does not say,
and `PlateNumbers.parsed` tells the caller which happened, so an engineer is asked
to type the numbers instead of being shown invented ones.

**What is verified and what is not.** The 3MF container handling is verified
against a real Bambu Studio project file. The *sliced* variant's `<plate>` element
comes from the documented shape and has not been seen from this farm's slicer;
G-code comment patterns are likewise documented shapes. Until a real sliced plate
has been through this, treat ``parsed=False`` as the expected case rather than the
exception — which is exactly why the caller must handle it well.
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree

#: Where Bambu Studio records what a slice produced, inside the 3MF zip.
SLICE_INFO = "Metadata/slice_info.config"

#: Anything larger is not a plate. A zip bomb expanding to fill the disk is the
#: failure this prevents; real plates are tens of megabytes.
MAX_MEMBER_BYTES = 256 * 1024 * 1024

_DURATION = {
    "hours": re.compile(r"(\d+)\s*h", re.I),
    # `(?!s)` so the `s` of `ms` does not read as minutes.
    "minutes": re.compile(r"(\d+)\s*m(?!s)", re.I),
    "seconds": re.compile(r"(\d+)\s*s", re.I),
}

#: Ordered by which number the farm actually wants.
#:
#: 1. **model printing time** (Bambu) — the print itself, which is what occupies a
#:    machine and what a schedule is built from.
#: 2. **estimated printing time** (PrusaSlicer and relatives).
#: 3. **total estimated time** (Bambu) — includes preheating and calibration, so a
#:    last resort: scheduling against it makes every job look longer than the bed
#:    is busy.
#:
#: Each stops at the next `;` so a line carrying two figures cannot bleed one into
#: the other.
TIME_PATTERNS = (
    re.compile(r"model printing time\s*[=:]\s*([^;\n\r]+)", re.I),
    re.compile(r"estimated printing time[^=:\n]*[=:]\s*([^;\n\r]+)", re.I),
    re.compile(r"total estimated time\s*[=:]\s*([^;\n\r]+)", re.I),
)

_FILAMENT_LIST = re.compile(r"filament used\s*\[g\]\s*=\s*([^\n\r]+)", re.I)
_FILAMENT_TOTAL = re.compile(r"total filament weight\s*\[g\]\s*[:=]\s*([\d.]+)", re.I)


@dataclass(frozen=True, slots=True)
class PlateNumbers:
    """What could be read from a plate, and whether it was enough."""

    print_minutes: int | None = None
    #: Grams per extruder, keyed by slot index as a string — matching the API's
    #: `filament_grams`. Per slot rather than a total because a multi-colour plate
    #: can exhaust one spool while the others are full.
    filament_grams: dict[str, Decimal] = field(default_factory=dict)

    @property
    def parsed(self) -> bool:
        """Both numbers, not either.

        Half-known numbers are the ones that get typed over carelessly, because
        the form looks filled in.
        """
        return self.print_minutes is not None and bool(self.filament_grams)


def parse_duration(text: str) -> int | None:
    """`1h 4m 30s`, `64m`, `1h4m` — the shapes slicers write for elapsed time."""
    found = {unit: pattern.search(text) for unit, pattern in _DURATION.items()}
    if not any(found.values()):
        return None

    def value(unit: str) -> int:
        match = found[unit]
        return int(match.group(1)) if match else 0

    # Seconds round *up*: a plate is never less work than the slicer said, and
    # rounding a job's duration down is how a schedule quietly slips.
    total = value("hours") * 60 + value("minutes") + math.ceil(value("seconds") / 60)
    return total if total > 0 else None


def parse_print_minutes(text: str) -> int | None:
    """Print minutes from a slicer's own comment line."""
    for pattern in TIME_PATTERNS:
        found = pattern.search(text)
        minutes = parse_duration(found.group(1)) if found else None
        if minutes is not None:
            return minutes
    return None


def parse_filament_grams(text: str) -> dict[str, Decimal]:
    """Grams per extruder, from a G-code comment."""
    grams: dict[str, Decimal] = {}

    # "; filament used [g] = 17.3, 4.2" — one entry per extruder, in order.
    listed = _FILAMENT_LIST.search(text)
    if listed:
        for index, value in enumerate(listed.group(1).split(",")):
            amount = _decimal_or_none(value)
            if amount is not None:
                grams[str(index)] = amount
        if grams:
            return grams

    # "; total filament weight [g] : 17.3" — a single figure, so slot 0.
    total = _FILAMENT_TOTAL.search(text)
    amount = _decimal_or_none(total.group(1)) if total else None
    if amount is not None:
        grams["0"] = amount
    return grams


def read_plate(content: bytes) -> PlateNumbers:
    """Read a plate, whatever container it arrived in.

    A 3MF is a zip and its metadata is XML; G-code is plain text. The desktop app
    could only do the second, so an uploaded 3MF always came back unparsed and the
    engineer typed both numbers. Reading the zip is the one thing this port gains
    from running on a server.
    """
    if _is_zip(content):
        return _read_3mf(content)
    return _read_gcode(content.decode("utf-8", errors="replace"))


# ------------------------------------------------------------------ internals


def _is_zip(content: bytes) -> bool:
    """A 3MF is a zip; `PK\\x03\\x04` is the local file header."""
    return content[:4] == b"PK\x03\x04"


def _read_gcode(text: str) -> PlateNumbers:
    return PlateNumbers(
        print_minutes=parse_print_minutes(text),
        filament_grams=parse_filament_grams(text),
    )


def _read_3mf(content: bytes) -> PlateNumbers:
    """Pull the numbers out of a 3MF's slice metadata.

    A *project* file — one saved before slicing — carries `slice_info.config` with
    a header and no `<plate>`. That is not an error and not a damaged file; it is a
    file that genuinely does not know how long anything takes, and the honest
    answer is to report nothing.
    """
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            info = archive.getinfo(SLICE_INFO)
            if info.file_size > MAX_MEMBER_BYTES:
                return PlateNumbers()
            xml = archive.read(SLICE_INFO)
    except (KeyError, zipfile.BadZipFile):
        return PlateNumbers()

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return PlateNumbers()

    plate = root.find("plate")
    if plate is None:
        return PlateNumbers()

    return PlateNumbers(
        print_minutes=_prediction_minutes(plate),
        filament_grams=_filament_slots(plate),
    )


def _prediction_minutes(plate: ElementTree.Element) -> int | None:
    """`<metadata key="prediction" value="4521"/>` — seconds, so convert."""
    for entry in plate.findall("metadata"):
        if entry.get("key") != "prediction":
            continue
        seconds = _decimal_or_none(entry.get("value") or "")
        if seconds is None:
            return None
        # `math.ceil`, not the `-(-x // n)` idiom: Decimal's `//` truncates toward
        # zero rather than flooring, so that trick rounds *down* here — which is
        # the very thing the rounding exists to prevent.
        minutes = math.ceil(seconds / 60)
        return minutes if minutes > 0 else None
    return None


def _filament_slots(plate: ElementTree.Element) -> dict[str, Decimal]:
    """`<filament id="1" used_g="22.73"/>` — one element per loaded slot.

    Bambu numbers filaments from 1 and the API keys slots from 0, so the id is
    shifted rather than trusted. An id that is not a number is skipped instead of
    being assigned a position it might not have.
    """
    grams: dict[str, Decimal] = {}
    for entry in plate.findall("filament"):
        amount = _decimal_or_none(entry.get("used_g") or "")
        if amount is None:
            continue
        raw_id = entry.get("id") or ""
        if not raw_id.isdigit():
            continue
        grams[str(max(0, int(raw_id) - 1))] = amount
    return grams


def _decimal_or_none(text: str) -> Decimal | None:
    """A positive decimal, or nothing. Zero grams is absence, not a measurement."""
    try:
        value = Decimal(text.strip())
    except (ArithmeticError, ValueError):
        return None
    return value if value > 0 else None


__all__ = [
    "SLICE_INFO",
    "TIME_PATTERNS",
    "PlateNumbers",
    "parse_duration",
    "parse_filament_grams",
    "parse_print_minutes",
    "read_plate",
]
