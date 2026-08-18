"""Wire shapes for the model library.

Separate from :mod:`schemas`, which serves the mesh and the plate cache. The
catalogue is a different reader with a different question — "what can I order?"
rather than "what did slicing produce?" — and keeping them apart is what stops the
public card growing an internal field because it happened to be nearby.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.catalog.catalogue import (
    CatalogModel,
    ModelCategory,
    SizeClass,
    Suitability,
)
from printorian.core.ids import EntityId


class MeasuredPrint(BaseModel):
    """What the last real print of this model actually took.

    Present only when a job has succeeded. Its absence is the signal the storefront
    needs in order to label an estimate as an estimate — which is why this is a
    nullable object rather than four nullable fields that a caller could half-read
    and present as fact.
    """

    at: datetime
    minutes: Decimal
    grams: Decimal
    #: What one unit was actually charged, from the pinned breakdown. `None` when
    #: the print was not part of a paid order — an internal test print, say.
    price: Decimal | None = None
    printer_name: str = ""


class SuitableMaterial(BaseModel):
    """One row of the popup's «Подходящие материалы» table.

    Three of its four columns come from three different places, which is the
    point: the catalogue says how well a material *suits* the part, pricing says
    what the difference costs, and inventory says whether any is on the shelf.
    Copying the last two onto the catalogue row would freeze numbers that change
    hourly.
    """

    code: str
    name: str = ""
    suitability: Suitability = Suitability.GOOD
    #: A caveat shown in place of the grade — «Не для улицы».
    note: str = ""
    is_recommended: bool = False

    #: Per-unit price difference against the recommended material.
    #:
    #: `None` when it cannot be *stated*: either nothing is recommended yet, or
    #: the model has never been printed and there is no measured mass to price
    #: the difference against. An estimate here would be the one fabricated
    #: number on a screen whose whole claim is that its figures are measured.
    price_delta: Decimal | None = None
    #: What the shop actually holds, in grams. `None` when the material is not in
    #: the catalogue of specs at all — which is different from holding zero.
    stock_grams: Decimal | None = None


class ModelHistory(BaseModel):
    """The «История модели» figures that are counted rather than stored.

    Both are `None` until there is something to count, and that is the whole
    discipline of this screen: a model nobody has finished printing has no success
    rate, and `100%` from a single job would read as a track record.
    """

    #: Share of *finished* prints that succeeded. Jobs still in the queue are not
    #: evidence either way, so they are excluded rather than counted as failures.
    success_rate: Decimal | None = None
    #: How many finished prints that share is measured over — without it the
    #: percentage is unreadable, because 100% of one is not 100% of two hundred.
    finished_prints: int = 0
    #: Share of orders placed by somebody who had ordered this model before.
    repeat_share: Decimal | None = None
    orders: int = 0


class PriceRung(BaseModel):
    """One row of the popup's «Цена по количеству» ladder.

    Priced by the real engine, not interpolated. Per-unit falls with quantity even
    with no volume discount configured, because the per-*job* costs — setting up
    the plate, buying in a material the shop does not hold — are spread over more
    units. That is the honest reason a ladder exists, and it survives the discount
    tiers being empty.
    """

    quantity: int
    unit_price: Decimal
    total: Decimal
    #: Hours the farm would promise for this quantity. See `ordering.promise`.
    lead_hours: Decimal
    #: Volume discount actually applied, as a percentage. Zero when no tier
    #: matched — the ladder still slopes, for the reason above.
    discount_percent: Decimal = Decimal(0)
    #: Whether this rung is the first at its discount tier — the kit's «ПОРОГ».
    is_threshold: bool = False


class CatalogCard(BaseModel):
    """One model as the grid draws it."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    slug: str
    code: str
    title: str
    summary: str = ""

    category: ModelCategory
    size_class: SizeClass
    #: The kit's six 0–10 spec bars. Editorial judgements, not measurements —
    #: zero means "not yet assessed" and the screen draws an empty bar.
    difficulty: int
    strength: int = 0
    accuracy: int = 0
    speed: int = 0
    supports: int = 0
    postprocessing: int = 0
    author: str = ""
    multicolor: bool
    tags: list[str] = Field(default_factory=list)
    #: Codes only — the grid's cards need nothing more, and a table of four
    #: columns per material on every card would be a page of joins for data
    #: nobody reads until a popup opens.
    materials: list[str] = Field(default_factory=list)
    #: The popup's tables. Empty on the grid, filled by `GET /catalog/{slug}`:
    #: both cost work the cards do not need, and one of them prices five times.
    suitable_materials: list[SuitableMaterial] = Field(default_factory=list)
    price_ladder: list[PriceRung] = Field(default_factory=list)
    #: What the ladder was priced on — the kit's «PETG-CF · 1 ЦВЕТ» aside.
    price_basis: str = ""
    #: Counted from real jobs and orders, so also popup-only.
    history: ModelHistory = Field(default_factory=ModelHistory)

    # -- geometry, read from the asset rather than copied onto the row
    volume_cm3: Decimal = Decimal(0)
    width_mm: Decimal = Decimal(0)
    depth_mm: Decimal = Decimal(0)
    height_mm: Decimal = Decimal(0)
    triangle_count: int = 0
    surface_area_cm2: Decimal = Decimal(0)
    #: From the mesh analysis. A part the slicer could not close cannot be
    #: priced, and the catalogue says so rather than hiding it.
    is_watertight: bool = True
    #: Warning codes from `analyse_stl` — thin walls and the like. Codes, never
    #: prose (ADR-0012); the storefront owns the wording.
    mesh_warnings: list[str] = Field(default_factory=list)
    #: Whether the farm holds bytes for this model, i.e. whether the 3D view
    #: and the STL download have anything to serve.
    has_geometry: bool = False
    created_at: datetime | None = None

    rating: Decimal = Decimal(0)
    rating_count: int = 0
    print_count: int = 0

    #: `None` until the farm has actually printed one. The screen must say
    #: "estimate" while this is null, and must never fill it from a prediction.
    measured: MeasuredPrint | None = None

    preview: dict[str, Any] = Field(default_factory=dict)
    license: str = ""
    version: str = ""
    published_at: datetime | None = None


class FacetCountView(BaseModel):
    value: str
    count: int


class CatalogTable(BaseModel):
    """A page of results, plus the counts every facet chip prints beside itself."""

    rows: list[CatalogCard]
    total: int
    #: Keyed by facet group — `cat`, `size`, `mat`, `colors`, `diff` — matching the
    #: `data-facet` values the kit puts in the markup, so the two cannot drift.
    counts: dict[str, list[FacetCountView]] = Field(default_factory=dict)


def _warnings_of(asset: object | None) -> list[str]:
    """Warning codes out of the stored mesh analysis, defensively.

    `mesh` is a JSON blob written by whichever version of `analyse_stl` ran at
    upload time, so its shape is not guaranteed by the column. A card must not
    fail to render because an old row spelled a key differently.
    """
    mesh = getattr(asset, "mesh", None)
    if not isinstance(mesh, dict):
        return []
    warnings = mesh.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(entry) for entry in warnings]


def _surface_area_cm2(asset: object | None) -> Decimal:
    """Surface area, in cm², from the stored analysis. Zero when unknown."""
    mesh = getattr(asset, "mesh", None)
    if not isinstance(mesh, dict):
        return Decimal(0)
    raw = mesh.get("surface_area_mm2")
    if raw is None:
        return Decimal(0)
    try:
        return (Decimal(str(raw)) / Decimal(100)).quantize(Decimal("0.01"))
    except (ArithmeticError, ValueError):
        return Decimal(0)


def card_of(model: CatalogModel) -> CatalogCard:
    """Assemble one card from a row and the asset it names.

    Geometry is read through the relationship rather than stored twice: a card
    whose dimensions disagreed with the mesh it points at would be wrong in a way
    no screen could detect.
    """
    asset = model.asset
    return CatalogCard(
        id=model.id,
        slug=model.slug,
        code=model.code,
        title=model.title,
        summary=model.summary,
        category=model.category,
        size_class=model.size_class,
        difficulty=model.difficulty,
        strength=model.strength,
        accuracy=model.accuracy,
        speed=model.speed,
        supports=model.supports,
        postprocessing=model.postprocessing,
        author=model.author,
        multicolor=model.multicolor,
        tags=list(model.tags or []),
        materials=sorted(entry.material_code for entry in model.materials),
        volume_cm3=asset.volume_cm3 if asset else Decimal(0),
        width_mm=asset.width_mm if asset else Decimal(0),
        depth_mm=asset.depth_mm if asset else Decimal(0),
        height_mm=asset.height_mm if asset else Decimal(0),
        triangle_count=asset.triangle_count if asset else 0,
        surface_area_cm2=_surface_area_cm2(asset),
        is_watertight=asset.is_watertight if asset else False,
        mesh_warnings=_warnings_of(asset),
        has_geometry=bool(asset and asset.storage_path),
        created_at=model.created_at,
        rating=model.rating,
        rating_count=model.rating_count,
        print_count=model.print_count,
        measured=(
            MeasuredPrint(
                at=model.last_printed_at,
                minutes=model.last_print_minutes,
                grams=model.last_print_grams or Decimal(0),
                price=model.last_price,
                printer_name=model.last_printer_name,
            )
            # Both halves checked, not just the timestamp: a row with a date and no
            # duration would render as a measurement of nothing.
            if model.last_printed_at is not None and model.last_print_minutes is not None
            else None
        ),
        preview=dict(model.preview or {}),
        license=model.license,
        version=model.version,
        published_at=model.published_at,
    )


class MaterialOffer(BaseModel):
    """One material an editor offers a model in.

    An object rather than a bare code because the kit's table asks three editorial
    questions per material, and a parallel list of grades would go out of step
    with the codes the first time somebody reordered one.
    """

    code: str = Field(min_length=1, max_length=80)
    suitability: Suitability = Suitability.GOOD
    note: str = Field(default="", max_length=60)
    is_recommended: bool = False


class CreateCatalogModel(BaseModel):
    """A new catalogue entry.

    `size_class`, `preview` and `search_text` are absent on purpose: each is
    derived from the geometry or the text, and letting a form set one would allow
    a catalogue that disagrees with the mesh it points at. See `curation`.
    """

    #: Appears in a URL a customer may share, so it is constrained to what reads
    #: as one rather than to what a database will accept.
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    code: str = Field(default="", max_length=80)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)

    category: ModelCategory
    #: The geometry this entry is about. Uploaded first, through
    #: `POST /catalog/geometry`, which answers with the measured facts.
    model_asset_id: EntityId

    difficulty: int = Field(default=0, ge=0, le=10)
    strength: int = Field(default=0, ge=0, le=10)
    accuracy: int = Field(default=0, ge=0, le=10)
    speed: int = Field(default=0, ge=0, le=10)
    supports: int = Field(default=0, ge=0, le=10)
    postprocessing: int = Field(default=0, ge=0, le=10)

    author: str = Field(default="", max_length=120)
    multicolor: bool = False
    tags: list[str] = Field(default_factory=list, max_length=20)
    materials: list[MaterialOffer] = Field(default_factory=list, max_length=20)
    license: str = Field(default="", max_length=80)
    version: str = Field(default="", max_length=40)

    #: Draft by default. A model appears in the shop window only when somebody
    #: says so, rather than the moment it is saved half-described.
    is_published: bool = False


class UpdateCatalogModel(BaseModel):
    """A partial edit.

    Every field optional, and read with `exclude_unset`: an absent field means
    "leave it alone", not "set it to null". Without that distinction a form that
    posts only the title blanks the description.
    """

    code: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=2000)
    category: ModelCategory | None = None
    model_asset_id: EntityId | None = None

    difficulty: int | None = Field(default=None, ge=0, le=10)
    strength: int | None = Field(default=None, ge=0, le=10)
    accuracy: int | None = Field(default=None, ge=0, le=10)
    speed: int | None = Field(default=None, ge=0, le=10)
    supports: int | None = Field(default=None, ge=0, le=10)
    postprocessing: int | None = Field(default=None, ge=0, le=10)

    author: str | None = Field(default=None, max_length=120)
    multicolor: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    materials: list[MaterialOffer] | None = Field(default=None, max_length=20)
    license: str | None = Field(default=None, max_length=80)
    version: str | None = Field(default=None, max_length=40)
    is_published: bool | None = None


class UploadedGeometry(BaseModel):
    """What an editor gets back after uploading a mesh.

    The measured facts come with it, so the form can show what the farm now knows
    about the part before the entry is created — and so a mesh that cannot be
    priced is visible as such at upload rather than after publishing.
    """

    model_asset_id: EntityId
    filename: str = ""
    triangle_count: int = 0
    volume_cm3: Decimal = Decimal(0)
    width_mm: Decimal = Decimal(0)
    depth_mm: Decimal = Decimal(0)
    height_mm: Decimal = Decimal(0)
    is_watertight: bool = False
    #: Whether an order may be placed against this geometry at all.
    is_priceable: bool = False
    size_class: SizeClass
