"""Inventory — material specs, physical lots, and where they are.

Public interface. The key split (docs/GLOSSARY.md): a **spec** is catalogue
identity and price; a **lot** is a physical spool with a mass and a location.
Status is derived from the lots, never stored.
"""

from printorian.contexts.inventory.headroom import MaterialStock, headroom
from printorian.contexts.inventory.movements import (
    MOVED_MOUNTED,
    MOVED_MOVED,
    MOVED_RECEIVED,
    MOVED_UNMOUNTED,
    MOVED_WRITTEN_OFF,
    MOVEMENT_REASONS,
)
from printorian.contexts.inventory.placement import PlacementService
from printorian.contexts.inventory.policies import (
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
    LotView,
    MaterialSpecView,
    MaterialTable,
    MovementView,
    PlaceLot,
    ScenarioMatch,
    StatusCount,
    WriteOffLot,
    ZoneView,
)
from printorian.contexts.inventory.service import InventoryService
from printorian.contexts.inventory.store_views import StoreViews

__all__ = [
    "MOVED_MOUNTED",
    "MOVED_MOVED",
    "MOVED_RECEIVED",
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
    "InventoryService",
    "Location",
    "LocationKind",
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
    "WriteOffLot",
    "ZoneView",
    "derive_status",
    "headroom",
]
