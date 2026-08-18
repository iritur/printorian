"""DTOs crossing the fleet boundary.

Note what is absent: there is no field anywhere here that carries an access code
outward. ADR-0014 makes it write-only, so views expose ``access_code_set`` — a
boolean — and nothing else. A DTO that *could* leak the secret is how it eventually
does.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.fleet.policies import ConnectionMode, MaintenanceKind
from printorian.core.ids import EntityId
from printorian.drivers import PrinterState


class AmsSlotView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit: int
    index: int
    material_type: str | None = None
    colour_hex: str | None = None
    remaining_percent: int | None = None
    lot_id: EntityId | None = None

    @property
    def is_loaded(self) -> bool:
        return self.material_type is not None


class ServiceOperationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    kind: MaintenanceKind
    interval_hours: int
    last_done_at_hours: Decimal
    last_done_at: datetime | None = None
    materials_used: list[str] = Field(default_factory=list)
    notes: str | None = None
    #: Computed against the printer's cumulative printing hours.
    is_due: bool = False
    hours_until_due: Decimal = Decimal(0)


class PrinterView(BaseModel):
    """One row of the scenario's printers table (item M2)."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    name: str
    brand: str
    model: str
    serial: str
    connection_mode: ConnectionMode
    host: str | None = None
    #: Whether a code is stored. The code itself is never returned (ADR-0014).
    access_code_set: bool = False

    state: PrinterState
    last_seen_at: datetime | None = None
    storage_available: bool = True

    # -- what the machine is doing right now, from its last report only
    progress_percent: int | None = None
    remaining_minutes: int | None = None
    #: Absolute finish time, so the table can show "printing · until 14:20" without
    #: every client recomputing it from a countdown that is already stale.
    eta: datetime | None = None
    current_job: str | None = None

    build_width_mm: Decimal
    build_depth_mm: Decimal
    build_height_mm: Decimal
    nozzle_diameter_mm: Decimal
    supports_multi_material: bool

    printed_hours: Decimal
    amortization_per_hour: Decimal = Decimal(0)
    nominal_power_kw: Decimal

    location: str | None = None
    is_active: bool = True
    needs_attention: bool = False
    maintenance_due: bool = False

    slots: list[AmsSlotView] = Field(default_factory=list)
    services: list[ServiceOperationView] = Field(default_factory=list)


class StatusCount(BaseModel):
    state: PrinterState
    count: int


class PrinterTable(BaseModel):
    """Rows plus the counter chips above them, as the scenario's table asks."""

    rows: list[PrinterView]
    counts: list[StatusCount]
    total: int
    attention: int


class CreatePrinter(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    brand: str = Field(default="bambu", max_length=40)
    model: str = Field(default="", max_length=80)
    serial: str = Field(default="", max_length=120)
    connection_mode: ConnectionMode = ConnectionMode.MANUAL
    host: str | None = None
    #: Write-only. Encrypted immediately and never echoed back.
    access_code: str | None = None

    build_width_mm: Decimal = Decimal(256)
    build_depth_mm: Decimal = Decimal(256)
    build_height_mm: Decimal = Decimal(256)
    nozzle_diameter_mm: Decimal = Decimal("0.4")
    supports_multi_material: bool = False

    acquisition_cost: Decimal = Decimal(0)
    expected_lifetime_hours: int = Field(default=20_000, ge=1)
    nominal_power_kw: Decimal = Decimal("0.35")
    location: str | None = None


class SetAccessCode(BaseModel):
    """Replacing a printer's credential.

    A separate request from the rest of the printer, so an ordinary edit cannot
    blank the code by omitting it — and so the audit trail distinguishes "someone
    renamed a printer" from "someone changed its credentials".
    """

    access_code: str = Field(min_length=1, max_length=64)


class MountLot(BaseModel):
    """Put a physical material lot into an AMS slot (the scenario's second location)."""

    unit: int = Field(default=0, ge=0)
    index: int = Field(ge=0)
    lot_id: EntityId


class CreateServiceOperation(BaseModel):
    kind: MaintenanceKind
    interval_hours: int = Field(default=500, ge=1)
    materials_used: list[str] = Field(default_factory=list)
    notes: str | None = None
