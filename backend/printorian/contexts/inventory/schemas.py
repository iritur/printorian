"""DTOs crossing the inventory boundary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.inventory.policies import (
    DryingState,
    LocationKind,
    MaterialStatus,
    StocktakeStatus,
)
from printorian.core.ids import EntityId


class LotView(BaseModel):
    #: ``populate_by_name`` so this can still be built by field name in a test or
    #: by hand; ``cell`` below is the one field whose source attribute is spelled
    #: differently from the field itself.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: EntityId
    label: str
    remaining_grams: Decimal
    location_kind: LocationKind
    #: The cell address, or ``None`` when the spool is not in one. Read from
    #: `MaterialLot.cell_address` rather than from `cell`, because the attribute of
    #: that name is the related row and the wire wants the address; the alias is
    #: what keeps `model_validate(lot)` working at every call site.
    cell: str | None = Field(default=None, validation_alias="cell_address")
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
    #: What the whole lot cost. Set by `procurement.receiving` off the receipt;
    #: null when a lot is entered by hand and nobody said. Deliberately **not**
    #: on `LotView`: `GET /materials` has no permission dependency and no session
    #: requirement, so a purchase price served there would reach an anonymous
    #: caller — the farm's buying price, on the storefront.
    purchase_price: Decimal | None = None
    #: The supplier's batch number, for recalls.
    lot_number: str | None = None


# -- the store: zones, cells, and the ledger --------------------------------


class CellView(BaseModel):
    """One `.hv-node` of the map.

    ``fill_percent`` is ``None`` wherever `capacity_lots` was never declared. The
    console then draws no fill bar at all — the treatment `StatusWall` already
    gives a null progress — rather than an empty one, which would read as "this
    cell is empty" about a cell holding four spools.
    """

    id: EntityId
    address: str
    zone_code: str
    capacity_lots: int | None = None
    lot_count: int
    fill_percent: Decimal | None = None
    is_active: bool = True


class ZoneView(BaseModel):
    """One zone of the cell map, with its measured conditions.

    ``fill_percent`` is occupied cells over **the cells that exist in this zone** —
    never over a target somebody configured. A denominator that is the roster
    rather than the observation makes the worst-stocked farm look the healthiest,
    and does it silently (CLAUDE.md §1).

    A zone with no cells reports ``None``, not ``0``: nothing has been declared
    there, which is a different fact from nothing being stored there, and «0 %»
    says the second.
    """

    id: EntityId
    code: str
    name: str
    temp_c: Decimal | None = None
    humidity_percent: Decimal | None = None
    cell_count: int
    occupied_cells: int
    fill_percent: Decimal | None = None
    cells: list[CellView] = Field(default_factory=list)


class CellMap(BaseModel):
    """The whole map in one response, so the zones and the totals cannot disagree.

    ``cells_total`` and ``occupied_total`` are counted from the same query that
    built the zones — the reason `MaterialTable` returns its chips beside its rows.
    """

    zones: list[ZoneView]
    cells_total: int
    occupied_total: int


class MovementView(BaseModel):
    """One row of the movements feed."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    lot_id: EntityId
    sequence: int
    #: Machine-readable; the client renders it (ADR-0012).
    reason: str
    grams: Decimal
    remaining_after: Decimal
    at: datetime
    actor_id: EntityId | None = None
    from_kind: LocationKind | None = None
    from_address: str | None = None
    to_kind: LocationKind | None = None
    to_address: str | None = None
    note: str | None = None


class DryingView(BaseModel):
    """One spool's drying state, computed at read time (`drying.drying_of`).

    ``valid_until`` and ``hours_left`` are set only where there is a mark to
    measure from. A spool never marked carries neither — a null here means "not
    measured", and the console draws a dash rather than a countdown from nowhere.
    """

    state: DryingState
    dried_at: datetime | None = None
    valid_until: datetime | None = None
    hours_left: Decimal | None = None


class StoredLot(LotView):
    """A lot as the cell panel lists it: the lot, plus what the shelf needs to know.

    A subclass rather than fields on `LotView`, because `LotView` is also the
    row `GET /materials` serves without a session, and the drying state needs a
    clock and the farm's settings to compute — neither of which that route has
    or should go and fetch for a catalogue read.
    """

    family: str
    received_at: datetime
    drying: DryingView


class CellDetail(BaseModel):
    """One cell: what is in it, oldest first, and how it got that way."""

    cell: CellView
    #: FIFO, oldest first — what the kit's «Партии в ячейке» panel shows, because
    #: the oldest spool is the one that should leave next.
    lots: list[StoredLot] = Field(default_factory=list)
    #: The window a drying mark is good for, so the panel can say «72 ч» beside
    #: the states. ``None`` when the rule is switched off, and the panel says so.
    drying_valid_hours: int | None = None
    movements: list[MovementView] = Field(default_factory=list)


class CreateStorageZone(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = ""
    #: Both nullable and both absent by default: a zone with no sensor has not been
    #: measured, and 0 °C is a reading (ADR-0007).
    temp_c: Decimal | None = None
    humidity_percent: Decimal | None = Field(default=None, ge=0, le=100)


class CreateStorageCell(BaseModel):
    zone_code: str = Field(min_length=1, max_length=16)
    address: str = Field(min_length=1, max_length=24)
    #: Absent means "nobody has said", and the map reports no fill for it. It is
    #: not the same as one, and defaulting it here is the whole ADR-0007 trap.
    capacity_lots: int | None = Field(default=None, ge=1)


class PlaceLot(BaseModel):
    address: str = Field(min_length=1, max_length=24)
    note: str | None = Field(default=None, max_length=200)


class DryLot(BaseModel):
    """Into the dryer, or out of it. The note is the «Основание» either way."""

    note: str | None = Field(default=None, max_length=200)


# -- the stocktake -----------------------------------------------------------


class OpenStocktake(BaseModel):
    """Start counting — the whole store, or one zone by its code."""

    zone_code: str | None = Field(default=None, min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=200)


class CountLine(BaseModel):
    """What was found on one spool. Zero is a count; absent is not."""

    counted_grams: Decimal = Field(ge=0)


class StocktakeLineView(BaseModel):
    lot_id: EntityId
    label: str
    family: str
    cell_address: str | None = None
    #: The book when the count opened.
    expected_grams: Decimal
    #: Null until somebody counted this spool — never zero by default.
    counted_grams: Decimal | None = None
    counted_at: datetime | None = None
    #: Written at close, signed: negative is a shortage. Null while open or uncounted.
    variance_grams: Decimal | None = None


class StocktakeSummary(BaseModel):
    """The panel's figures. Every count here is over the lines that exist."""

    id: EntityId
    number: str
    status: StocktakeStatus
    zone_code: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    #: Lines lined up at opening — the denominator of «проверено».
    positions: int
    counted: int
    matched: int
    short: int
    over: int


class StocktakeDetail(StocktakeSummary):
    note: str | None = None
    lines: list[StocktakeLineView] = Field(default_factory=list)


class StocktakeValue(BaseModel):
    """The shortage and the surplus in money — behind `VIEW_FINANCIALS`, on its own route.

    Costed only where receiving recorded a price on the spool; the lines it could
    not cost are counted, not costed at nought.
    """

    id: EntityId
    number: str
    short_value: Decimal
    over_value: Decimal
    unpriced_lines: int


class WriteOffLot(BaseModel):
    """Take mass off a reel for good.

    ``grams`` is bounded above zero here so the refusal is structural, and bounded
    by what remains in `placement.write_off` — which has to read the row to know.
    """

    grams: Decimal = Field(gt=0)
    note: str | None = Field(default=None, max_length=200)
