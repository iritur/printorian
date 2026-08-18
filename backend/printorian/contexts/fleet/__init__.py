"""Fleet — the printers, what they can do, and what they are doing.

Public interface.

Two rules define this context:

* a printer's access code is stored encrypted and is **write-only** across the API
  (ADR-0014) — it can be set and replaced, never read back;
* state is only ever what a machine reported. A printer that cannot be reached is
  ``OFFLINE``, never assumed idle (ADR-0007).
"""

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
    "AmsSlotView",
    "ConnectionMode",
    "CreatePrinter",
    "CreateServiceOperation",
    "Eligibility",
    "FleetService",
    "JobRequirements",
    "MaintenanceKind",
    "MountLot",
    "PrinterCapability",
    "PrinterTable",
    "PrinterView",
    "ServiceOperationView",
    "SetAccessCode",
    "StatusCount",
    "amortization_per_hour",
    "can_take",
    "needs_attention",
]
