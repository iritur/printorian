"""Inventory — material specs, physical lots, and where they are.

Public interface. The key split (docs/GLOSSARY.md): a **spec** is catalogue
identity and price; a **lot** is a physical spool with a mass and a location.
Status is derived from the lots, never stored.
"""

from printorian.contexts.inventory.policies import (
    Location,
    LocationKind,
    MaterialStatus,
    derive_status,
)
from printorian.contexts.inventory.schemas import (
    CreateMaterialLot,
    CreateMaterialSpec,
    LotView,
    MaterialSpecView,
    MaterialTable,
    ScenarioMatch,
    StatusCount,
)
from printorian.contexts.inventory.service import InventoryService

__all__ = [
    "CreateMaterialLot",
    "CreateMaterialSpec",
    "InventoryService",
    "Location",
    "LocationKind",
    "LotView",
    "MaterialSpecView",
    "MaterialStatus",
    "MaterialTable",
    "ScenarioMatch",
    "StatusCount",
    "derive_status",
]
