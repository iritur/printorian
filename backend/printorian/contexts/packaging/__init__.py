"""Packing — the last post before the van.

Public interface. The defining rule: **the deadline is the courier's, not the
customer's.** A promise three days out is irrelevant at 17:20 when the pickup is
at 19:30, so everything here — the board's order, the header's countdown, a
card's urgency — is measured against `cutoff_at`.

The gauges themselves are `postproduction`'s, imported through its public
interface rather than copied: a packer and a finisher are measured by the same
farm, and two definitions of "on norm" would be one definition too many.
"""

from printorian.contexts.packaging.analytics import metrics, scorecards
from printorian.contexts.packaging.board import (
    BOARD_LIMIT,
    COLUMNS,
    OPEN,
    board_columns,
    next_cutoff,
    pickups,
    shift_kpi,
)
from printorian.contexts.packaging.catalogue import PackingCatalogue
from printorian.contexts.packaging.events import (
    DiscrepancyFound,
    ParcelHeld,
    ParcelRaised,
    ParcelShipped,
    ParcelStatusChanged,
)
from printorian.contexts.packaging.models import (
    PackInstruction,
    PackInstructionStep,
    PackStep,
    PackTask,
    PackUse,
    Tara,
)
from printorian.contexts.packaging.policies import (
    DIM_DIVISOR,
    ENCLOSURES,
    SOON_MINUTES,
    STATS_DAYS,
    THIN_WALL_MM,
    TRANSITIONS,
    Dims,
    HoldReason,
    PackStatus,
    TaraKind,
    assert_transition,
    batch_box,
    can_transition,
    chargeable_grams,
    fits,
    needs_wrap,
    stack_box,
    volumetric_grams,
)
from printorian.contexts.packaging.schemas import (
    ChooseTara,
    CreatePackStep,
    CreatePackTask,
    CreateTara,
    HoldParcel,
    PackBoard,
    PackColumn,
    PackKpi,
    PackLine,
    PackMetrics,
    PackScore,
    PackStepView,
    PackView,
    PickupView,
    PublishInstruction,
    ReportDiscrepancy,
    TaraRow,
    TickStep,
    Weigh,
)
from printorian.contexts.packaging.service import PackagingService
from printorian.contexts.packaging.tara import enclosures, recommend, tara_accuracy, tara_rows
from printorian.contexts.packaging.views import view_of

__all__ = [
    "BOARD_LIMIT",
    "COLUMNS",
    "DIM_DIVISOR",
    "ENCLOSURES",
    "OPEN",
    "SOON_MINUTES",
    "STATS_DAYS",
    "THIN_WALL_MM",
    "TRANSITIONS",
    "ChooseTara",
    "CreatePackStep",
    "CreatePackTask",
    "CreateTara",
    "Dims",
    "DiscrepancyFound",
    "HoldParcel",
    "HoldReason",
    "PackBoard",
    "PackColumn",
    "PackInstruction",
    "PackInstructionStep",
    "PackKpi",
    "PackLine",
    "PackMetrics",
    "PackScore",
    "PackStatus",
    "PackStep",
    "PackStepView",
    "PackTask",
    "PackUse",
    "PackView",
    "PackagingService",
    "PackingCatalogue",
    "ParcelHeld",
    "ParcelRaised",
    "ParcelShipped",
    "ParcelStatusChanged",
    "PickupView",
    "PublishInstruction",
    "ReportDiscrepancy",
    "Tara",
    "TaraKind",
    "TaraRow",
    "TickStep",
    "Weigh",
    "assert_transition",
    "batch_box",
    "board_columns",
    "can_transition",
    "chargeable_grams",
    "enclosures",
    "fits",
    "metrics",
    "needs_wrap",
    "next_cutoff",
    "pickups",
    "recommend",
    "scorecards",
    "shift_kpi",
    "stack_box",
    "tara_accuracy",
    "tara_rows",
    "view_of",
    "volumetric_grams",
]
