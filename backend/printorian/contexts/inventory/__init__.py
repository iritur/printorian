"""Inventory — material specs, physical lots, and where they are.

Public interface. The key split (docs/GLOSSARY.md): a **spec** is catalogue
identity and price; a **lot** is a physical spool with a mass and a location.
Status is derived from the lots, never stored.
"""

from printorian.contexts.inventory.drying import (
    HYGROSCOPIC_FAMILIES,
    DryingPolicy,
    DryingService,
    drying_of,
    needs_drying,
)
from printorian.contexts.inventory.headroom import MaterialStock, headroom
from printorian.contexts.inventory.movements import (
    MOVED_DRIED,
    MOVED_MOUNTED,
    MOVED_MOVED,
    MOVED_RECEIVED,
    MOVED_TO_DRYER,
    MOVED_UNMOUNTED,
    MOVED_WRITTEN_OFF,
    MOVEMENT_REASONS,
)
from printorian.contexts.inventory.placement import PlacementService
from printorian.contexts.inventory.policies import (
    DryingState,
    Location,
    LocationKind,
    MaterialStatus,
    derive_status,
)
from printorian.contexts.inventory.schemas import (
    CellDetail,
    CellMap,
    CellView,
    CreateMaterialLot,
    CreateMaterialSpec,
    CreateStorageCell,
    CreateStorageZone,
    DryingView,
    DryLot,
    LotView,
    MaterialSpecView,
    MaterialTable,
    MovementView,
    PlaceLot,
    ScenarioMatch,
    StatusCount,
    StoredLot,
    WriteOffLot,
    ZoneView,
)
from printorian.contexts.inventory.service import InventoryService
from printorian.contexts.inventory.store_measures import (
    DeadStockLot,
    DeadStockReport,
    LotHistory,
    TurnoverReport,
    TurnoverRow,
    dead_stock,
    lot_histories,
    turnover,
)
from printorian.contexts.inventory.store_views import StoreViews

__all__ = [
    "HYGROSCOPIC_FAMILIES",
    "MOVED_DRIED",
    "MOVED_MOUNTED",
    "MOVED_MOVED",
    "MOVED_RECEIVED",
    "MOVED_TO_DRYER",
    "MOVED_UNMOUNTED",
    "MOVED_WRITTEN_OFF",
    "MOVEMENT_REASONS",
    "CellDetail",
    "CellMap",
    "CellView",
    "CreateMaterialLot",
    "CreateMaterialSpec",
    "CreateStorageCell",
    "CreateStorageZone",
    "DeadStockLot",
    "DeadStockReport",
    "DryLot",
    "DryingPolicy",
    "DryingService",
    "DryingState",
    "DryingView",
    "InventoryService",
    "Location",
    "LocationKind",
    "LotHistory",
    "LotView",
    "MaterialSpecView",
    "MaterialStatus",
    "MaterialStock",
    "MaterialTable",
    "MovementView",
    "PlaceLot",
    "PlacementService",
    "ScenarioMatch",
    "StatusCount",
    "StoreViews",
    "StoredLot",
    "TurnoverReport",
    "TurnoverRow",
    "WriteOffLot",
    "ZoneView",
    "dead_stock",
    "derive_status",
    "drying_of",
    "headroom",
    "lot_histories",
    "needs_drying",
    "turnover",
]
