"""DTOs crossing the inventory boundary."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.inventory.policies import LocationKind, MaterialStatus
from printorian.core.ids import EntityId


class LotView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    label: str
    remaining_grams: Decimal
    location_kind: LocationKind
    shelf: str | None = None
    printer_id: EntityId | None = None
    ams_unit: int | None = None
    ams_slot: int | None = None


class MaterialSpecView(BaseModel):
    """One row of the scenario's materials table."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    code: str
    name: str
    family: str
    color_name: str
    color_hex: str
    density_g_per_cm3: Decimal
    sell_price_per_gram: Decimal
    purchase_price_per_1000m: Decimal | None = None
    tensile_mpa: Decimal | None = None
    hdt_c: Decimal | None = None
    is_flexible: bool = False
    is_outdoor_safe: bool = False

    #: Derived, never stored — see policies.derive_status.
    status: MaterialStatus
    total_remaining_grams: Decimal
    lot_count: int
    lots: list[LotView] = Field(default_factory=list)


class StatusCount(BaseModel):
    """One of the counter chips shown above the materials table."""

    status: MaterialStatus
    count: int


class MaterialTable(BaseModel):
    """Rows plus the counts the table header needs, in one response.

    Returned together so the chips and the rows can never disagree about the same
    moment in time.
    """

    rows: list[MaterialSpecView]
    counts: list[StatusCount]
    total: int


class ScenarioMatch(BaseModel):
    """A material recommended for a usage scenario, and why."""

    spec: MaterialSpecView
    score: int
    #: Machine-readable reasons, e.g. ``["match.tensile", "match.in_stock"]``.
    reasons: list[str] = Field(default_factory=list)


class CreateMaterialSpec(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    family: str = Field(min_length=1, max_length=40)
    color_name: str = ""
    color_hex: str = "#888888"
    density_g_per_cm3: Decimal = Decimal("1.24")
    sell_price_per_gram: Decimal = Decimal("2.40")
    purchase_price_per_1000m: Decimal | None = None
    tensile_mpa: Decimal | None = None
    hdt_c: Decimal | None = None
    is_flexible: bool = False
    is_outdoor_safe: bool = False


class CreateMaterialLot(BaseModel):
    spec_code: str
    label: str = ""
    initial_grams: Decimal = Decimal(1000)
    remaining_grams: Decimal | None = None
    shelf: str | None = None
