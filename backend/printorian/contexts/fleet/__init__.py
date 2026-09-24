"""Fleet — the printers, what they can do, and what they are doing.

Public interface.

Two rules define this context:

* a printer's access code is stored encrypted and is **write-only** across the API
  (ADR-0014) — it can be set and replaced, never read back;
* state is only ever what a machine reported. A printer that cannot be reached is
  ``OFFLINE``, never assumed idle (ADR-0007).
"""

from printorian.contexts.fleet.labels import brands_for
from printorian.contexts.fleet.maintenance import (
    MaintenanceDefault,
    MaintenanceDefaults,
    default_maintenance,
    maintenance_to_json,
    parse_maintenance_defaults,
    seed_service_card,
)
from printorian.contexts.fleet.measures import (
    MAX_BUCKETS,
    MAX_WINDOW_HOURS,
    FleetBucket,
    FleetMetrics,
    Grain,
    MetricWindow,
    PrinterBucket,
    PrinterMetrics,
    fleet_metrics,
    observed_by_printer,
    printer_metrics,
    resolve_window,
)
from printorian.contexts.fleet.occupancy import (
    HEAT_DAYS,
    FleetOccupancy,
    HeatCell,
    HeatRow,
    Occupancy,
    hourly_load,
    occupancy,
)
from printorian.contexts.fleet.policies import (
    ConnectionMode,
    Eligibility,
    JobRequirements,
    MaintenanceKind,
    PrinterCapability,
    amortization_per_hour,
    can_take,
    needs_attention,
)
from printorian.contexts.fleet.schemas import (
    AmsSlotView,
    CreatePrinter,
    CreateServiceOperation,
    MountLot,
    PrinterTable,
    PrinterView,
    ServiceOperationView,
    SetAccessCode,
    StatusCount,
)
from printorian.contexts.fleet.service import FleetService

__all__ = [
    "HEAT_DAYS",
    "MAX_BUCKETS",
    "MAX_WINDOW_HOURS",
    "AmsSlotView",
    "ConnectionMode",
    "CreatePrinter",
    "CreateServiceOperation",
    "Eligibility",
    "FleetBucket",
    "FleetMetrics",
    "FleetOccupancy",
    "FleetService",
    "Grain",
    "HeatCell",
    "HeatRow",
    "JobRequirements",
    "MaintenanceDefault",
    "MaintenanceDefaults",
    "MaintenanceKind",
    "MetricWindow",
    "MountLot",
    "Occupancy",
    "PrinterBucket",
    "PrinterCapability",
    "PrinterMetrics",
    "PrinterTable",
    "PrinterView",
    "ServiceOperationView",
    "SetAccessCode",
    "StatusCount",
    "amortization_per_hour",
    "brands_for",
    "can_take",
    "default_maintenance",
    "fleet_metrics",
    "hourly_load",
    "maintenance_to_json",
    "needs_attention",
    "observed_by_printer",
    "occupancy",
    "parse_maintenance_defaults",
    "printer_metrics",
    "resolve_window",
    "seed_service_card",
]
