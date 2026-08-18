"""The materials table, and usage-scenario recommendation."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, status

from printorian.api.deps import DbSession, requires
from printorian.contexts.identity import Permission
from printorian.contexts.inventory import (
    CreateMaterialLot,
    CreateMaterialSpec,
    InventoryService,
    LotView,
    MaterialSpecView,
    MaterialTable,
    ScenarioMatch,
)

router = APIRouter(prefix="/materials", tags=["materials"])


@router.get("")
async def materials_table(db: DbSession, family: str | None = None) -> MaterialTable:
    """Rows plus status counts for the scenario's materials screen.

    Open to any caller: the storefront configurator needs the catalogue to offer
    materials and colours. Purchase prices are management data and are only
    populated for staff-facing views in Phase 2.
    """
    return await InventoryService(db).table(family=family)


@router.get("/recommend")
async def recommend(
    db: DbSession,
    min_tensile_mpa: Decimal | None = None,
    min_hdt_c: Decimal | None = None,
    requires_flexible: bool = False,
    requires_outdoor: bool = False,
    limit: int = 5,
) -> list[ScenarioMatch]:
    """Choose materials for a usage scenario (scenario option 2a)."""
    return await InventoryService(db).recommend(
        min_tensile_mpa=min_tensile_mpa,
        min_hdt_c=min_hdt_c,
        requires_flexible=requires_flexible,
        requires_outdoor=requires_outdoor,
        limit=limit,
    )


@router.get("/{code}")
async def get_material(code: str, db: DbSession) -> MaterialSpecView:
    return await InventoryService(db).get_by_code(code)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires(Permission.MANAGE_INVENTORY))],
)
async def create_material(data: CreateMaterialSpec, db: DbSession) -> MaterialSpecView:
    return await InventoryService(db).create_spec(data)


@router.post(
    "/lots",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires(Permission.MANAGE_INVENTORY))],
)
async def add_lot(data: CreateMaterialLot, db: DbSession) -> LotView:
    return await InventoryService(db).add_lot(data)
