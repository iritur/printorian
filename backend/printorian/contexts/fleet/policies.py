"""Fleet rules: what a printer can take, and when it needs attention.

The eligibility check here is the one the Phase 4 scheduler will filter on, written
now because the fleet is the thing that knows the answer. Keeping it as a pure
function means the scheduler can be tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from printorian.core.colors import is_multicolor
from printorian.drivers import PrinterState


class ConnectionMode(StrEnum):
    """How the farm reaches a machine.

    ``MANUAL`` is a first-class citizen (ADR-0011): it is how an Elegoo, or any
    printer whose driver does not exist yet, takes part in the fleet.
    """

    LAN = "lan"
    CLOUD = "cloud"
    MANUAL = "manual"
    MOCK = "mock"


class MaintenanceKind(StrEnum):
    """Service operations, per the scenario's printer service card."""

    NOZZLE_CHANGE = "nozzle_change"
    BELT_TENSION = "belt_tension"
    LUBRICATION = "lubrication"
    BED_LEVEL = "bed_level"
    FILTER_CHANGE = "filter_change"
    DEEP_CLEAN = "deep_clean"


@dataclass(frozen=True, slots=True, kw_only=True)
class JobRequirements:
    """What a job needs from a machine, as hard constraints."""

    width_mm: Decimal
    depth_mm: Decimal
    height_mm: Decimal
    material_type: str
    #: One entry per colour. More than one needs multi-material capability.
    colors: tuple[str, ...] = ()
    nozzle_diameter_mm: Decimal | None = None
    grams_required: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class PrinterCapability:
    """What a machine can physically do, as the scheduler sees it."""

    printer_id: str
    state: PrinterState
    width_mm: Decimal
    depth_mm: Decimal
    height_mm: Decimal
    nozzle_diameter_mm: Decimal
    supports_multi_material: bool
    #: Materials currently loaded and reachable, as (type, colour, grams left).
    loaded: tuple[tuple[str, str, Decimal], ...] = ()
    in_maintenance: bool = False
    #: No writable storage means the plate cannot be delivered — found on real
    #: hardware in Phase 0, and a printer in that condition is not capacity.
    storage_available: bool = True


@dataclass(frozen=True, slots=True)
class Eligibility:
    """Whether a printer can take a job, and if not, exactly why.

    The reasons are machine-readable and kept even on success, because "why did job
    X go to printer Y" has to be answerable later — and answering it means knowing
    which machines were rejected and on what grounds.
    """

    eligible: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def ok(cls) -> Eligibility:
        return cls(True, ())

    @classmethod
    def no(cls, *reasons: str) -> Eligibility:
        return cls(False, reasons)


def can_take(printer: PrinterCapability, job: JobRequirements) -> Eligibility:
    """Hard constraints only. Preference and scoring belong to the scheduler."""
    reasons: list[str] = []

    if printer.in_maintenance:
        reasons.append("reject.in_maintenance")
    if not printer.state.accepts_job:
        reasons.append("reject.busy")
    if not printer.storage_available:
        reasons.append("reject.no_storage")

    if (
        job.width_mm > printer.width_mm
        or job.depth_mm > printer.depth_mm
        or job.height_mm > printer.height_mm
    ):
        reasons.append("reject.build_volume")

    if job.nozzle_diameter_mm is not None and job.nozzle_diameter_mm != printer.nozzle_diameter_mm:
        reasons.append("reject.nozzle")

    # Distinct filaments, not slots: a plate whose two slots hold the same colour
    # prints on any machine. See `core.colors`.
    if is_multicolor(job.colors) and not printer.supports_multi_material:
        reasons.append("reject.no_multi_material")

    # The material must be loaded *and* have enough left. A machine holding 3 g of
    # the right filament is not a machine that can print this.
    available = [
        grams
        for material, _colour, grams in printer.loaded
        if material.casefold() == job.material_type.casefold()
    ]
    if not available:
        reasons.append("reject.material_not_loaded")
    elif max(available) < job.grams_required:
        reasons.append("reject.insufficient_material")

    # Every requested colour must actually be in a slot.
    loaded_colors = {colour.casefold() for _m, colour, _g in printer.loaded}
    missing = [c for c in job.colors if c.casefold() not in loaded_colors]
    if missing and job.colors:
        reasons.append("reject.colour_not_loaded")

    return Eligibility.ok() if not reasons else Eligibility.no(*reasons)


def needs_attention(state: PrinterState, *, maintenance_due: bool) -> bool:
    """Whether a machine should appear in the personnel dashboard."""
    return state in {PrinterState.ERROR, PrinterState.OFFLINE, PrinterState.FINISHED} or (
        maintenance_due
    )


def amortization_per_hour(acquisition_cost: Decimal, expected_lifetime_hours: int) -> Decimal:
    """Cost per printing hour, for the pricing rate snapshot and the P&L.

    Deliberately based on *printing* hours rather than calendar time: a machine that
    sits idle has not worn out, and charging idle hours to jobs would make a quiet
    week look like an expensive one.
    """
    if expected_lifetime_hours <= 0:
        return Decimal(0)
    return (acquisition_cost / Decimal(expected_lifetime_hours)).quantize(Decimal("0.01"))
