"""The model library, as the storefront's catalogue screen reads it.

Public: the catalogue is a shop window. Unpublished models are visible only to
somebody who may manage the library, and that is the *only* difference between
what a customer sees and what an editor does — a second, staff-only shape of the
same screen is how the two drift.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from printorian.api.deps import (
    AppClock,
    AppSettings,
    Cpu,
    DbSession,
    FarmSettings,
    Models,
    OptionalActor,
    rate_limited,
    requires,
)
from printorian.api.routers._catalog_panels import (
    _history,
    _price_ladder,
    _suitable_materials,
)
from printorian.contexts.catalog.browse import (
    DIFFICULTY_BANDS,
    Facets,
    ModelCatalogue,
    SortKey,
)
from printorian.contexts.catalog.catalogue import (
    ModelCategory,
    SizeClass,
    size_class_of,
)
from printorian.contexts.catalog.catalogue_schemas import (
    CatalogCard,
    CatalogTable,
    CreateCatalogModel,
    FacetCountView,
    UpdateCatalogModel,
    UploadedGeometry,
    card_of,
)
from printorian.contexts.catalog.curation import CatalogCuration
from printorian.contexts.identity import Actor, Permission
from printorian.core.errors import PayloadTooLargeError

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _may_see_drafts(actor: Actor | None) -> bool:
    """Whether this caller sees unpublished models.

    An anonymous visitor sees the shop window. An editor sees the shop window
    *plus* their drafts — the same screen, one predicate wider, rather than a
    second staff-only view of the catalogue that would drift from this one.
    """
    return actor is not None and actor.can(Permission.MANAGE_LIBRARY)


@router.get("")
async def browse(
    db: DbSession,
    actor: OptionalActor = None,
    q: str = "",
    # Repeated query parameters, one per ticked box: `?cat=func&cat=case`. That
    # spelling is what makes "OR within a group" the obvious reading of the URL,
    # and it is what a form with checkboxes submits without any client help.
    cat: Annotated[list[ModelCategory] | None, Query()] = None,
    size: Annotated[list[SizeClass] | None, Query()] = None,
    mat: Annotated[list[str] | None, Query()] = None,
    colors: Annotated[list[str] | None, Query()] = None,
    diff: Annotated[list[str] | None, Query()] = None,
    sort: SortKey = SortKey.POPULAR,
    #: `None` means "however this key opens" — cost-like ascending, quality-like
    #: descending. A value is the reader having clicked the active key to flip it.
    desc: bool | None = None,
    limit: int = Query(default=24, ge=1, le=96),
    offset: int = Query(default=0, ge=0),
) -> CatalogTable:
    """Search, facets and sort in one pass.

    One request, not three: the kit is explicit that sorting must never clear a
    filter and searching must never ignore the facets, and the surest way to keep
    that promise is to give the client no way to ask for them separately.
    """
    facets = Facets(
        categories=frozenset(cat or ()),
        sizes=frozenset(size or ()),
        materials=frozenset(mat or ()),
        multicolor=frozenset(value == "multi" for value in (colors or ())),
        # Unknown band names are dropped rather than 422'd: a stale bookmark
        # should show the catalogue, not an error page.
        difficulties=frozenset(band for band in (diff or ()) if band in DIFFICULTY_BANDS),
    )

    page = await ModelCatalogue(db).search(
        text=q,
        facets=facets,
        sort=sort,
        descending=desc,
        limit=limit,
        offset=offset,
        include_unpublished=_may_see_drafts(actor),
    )

    return CatalogTable(
        rows=[card_of(model) for model in page.rows],
        total=page.total,
        counts={
            group: [FacetCountView(value=entry.value, count=entry.count) for entry in entries]
            for group, entries in page.counts.items()
        },
    )


@router.get("/{slug}")
async def model_detail(
    slug: str,
    db: DbSession,
    models: Models,
    cpu: Cpu,
    settings: FarmSettings,
    actor: OptionalActor = None,
) -> CatalogCard:
    """One model, by the slug that appears in its URL.

    Richer than a row of the grid: this is what the popup draws, and it carries
    the suitable-materials table the cards have no room for.
    """
    model = await ModelCatalogue(db).get(slug, include_unpublished=_may_see_drafts(actor))
    card = card_of(model)
    card.suitable_materials = await _suitable_materials(db, model)
    card.price_ladder, card.price_basis = await _price_ladder(db, models, model, cpu, settings)
    card.history = await _history(db, model)
    return card


def safe_filename(name: str) -> str:
    """A filename safe to put in a header.

    Public because `_account_*` serves a customer's own uploads under the same
    rule, and two escapers for one header is one escaper that will be forgotten.

    Quotes and newlines in a ``Content-Disposition`` are a header-injection
    vector, and this name came from a customer's upload.
    """
    forbidden = frozenset(['"', "\\", "\r", "\n"])
    cleaned = "".join(c for c in name if c.isprintable() and c not in forbidden)
    return cleaned[:200] or "model.stl"


@router.get("/{slug}/model", response_class=Response)
async def model_geometry(
    slug: str,
    db: DbSession,
    models: Models,
    actor: OptionalActor = None,
) -> Response:
    """The mesh itself, for the 3D view and the download button.

    Public, like the rest of the catalogue: a shop window that will not let you
    look at the thing it is selling is not a shop window. This is geometry the
    farm has chosen to *publish* — a customer's own upload is not reachable here,
    only through the job endpoints, which require `prepare_plate`.

    `inline` rather than `attachment`: the browser fetches this to render it, and
    the download button in the UI supplies its own filename.
    """
    model = await ModelCatalogue(db).get(slug, include_unpublished=_may_see_drafts(actor))
    content, filename = await models.content(model.model_asset_id)
    return Response(
        content=content,
        media_type="model/stl",
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename(filename)}"',
            # Content-addressed storage: the bytes behind a slug never change
            # without the asset changing, so this is safe to keep for a while.
            "Cache-Control": "public, max-age=3600",
        },
    )


# ---------------------------------------------------------------- curation
#
# Everything below is staff-only, and gated on `MANAGE_LIBRARY` — which Engineer,
# Manager and Owner hold and Customer does not. The gate is declared per route
# rather than on the router, because the reads above are deliberately public: a
# shop window nobody can look into is not a shop window.


@router.post(
    "/geometry",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited("upload", lambda s: s.upload_rate_per_minute))],
)
async def upload_geometry(
    models: Models,
    settings: AppSettings,
    actor: Annotated[Actor, Depends(requires(Permission.MANAGE_LIBRARY))],
    file: Annotated[UploadFile, File()],
) -> UploadedGeometry:
    """Store a mesh and answer with what it measures.

    The first half of adding a model. Content-addressed, so uploading a file the
    farm already holds costs one hash and returns the existing asset — which is
    also what lets a catalogue entry and a customer's own upload share a prepared
    plate.

    The measured facts come back with it so the form can show them *before* the
    entry is created, and so a mesh that cannot be priced is visible as such now
    rather than after it is published.
    """
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        # This endpoint had no size check at all. It is staff-only, which lowered
        # the odds rather than the cost: a mesh nobody meant to send is still a
        # mesh the process parses and the disk keeps.
        raise PayloadTooLargeError(
            "error.catalog.upload_too_large",
            size=len(data),
            limit=settings.max_upload_bytes,
        )

    # `ingest` measures off the event loop through the same gate (`core.cpu`), and
    # decides for itself whether the format can be measured at all — a 3MF is
    # stored and served but never parsed, and that rule has one home.
    asset = await models.ingest(
        data, filename=file.filename or "model.stl", uploaded_by=actor.user_id
    )
    return UploadedGeometry(
        model_asset_id=asset.id,
        filename=asset.original_filename,
        triangle_count=asset.triangle_count,
        volume_cm3=asset.volume_cm3,
        width_mm=asset.width_mm,
        depth_mm=asset.depth_mm,
        height_mm=asset.height_mm,
        is_watertight=asset.is_watertight,
        is_priceable=asset.is_watertight and asset.volume_cm3 > 0,
        size_class=size_class_of(asset.width_mm, asset.depth_mm, asset.height_mm),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires(Permission.MANAGE_LIBRARY))],
)
async def create_model(
    data: CreateCatalogModel, db: DbSession, models: Models, clock: AppClock
) -> CatalogCard:
    """Add a model to the library.

    Answers with the same shape `GET /catalog/{slug}` does, table included, so an
    editor's form can render the result of a save without a second request — and
    so the two endpoints cannot drift into returning different things.
    """
    # The bytes are read here rather than inside the service so that curation
    # stays free of the object store, the same way `browse` is.
    geometry, _ = await models.content(data.model_asset_id)
    model = await CatalogCuration(db, clock).create(data, geometry=geometry)
    card = card_of(model)
    card.suitable_materials = await _suitable_materials(db, model)
    return card


@router.patch("/{slug}", dependencies=[Depends(requires(Permission.MANAGE_LIBRARY))])
async def update_model(
    slug: str,
    data: UpdateCatalogModel,
    db: DbSession,
    models: Models,
    clock: AppClock,
) -> CatalogCard:
    """Edit a model. Absent fields are left alone."""
    geometry: bytes | None = None
    if data.model_asset_id is not None:
        # Only fetched when the geometry actually moved — the card's drawing is
        # projected from it, and re-reading megabytes on a title change would be
        # work for nothing.
        geometry, _ = await models.content(data.model_asset_id)
    model = await CatalogCuration(db, clock).update(slug, data, geometry=geometry)
    card = card_of(model)
    card.suitable_materials = await _suitable_materials(db, model)
    return card


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires(Permission.MANAGE_LIBRARY))],
)
async def delete_model(slug: str, db: DbSession, clock: AppClock) -> None:
    """Remove an entry. The mesh behind it is left for retention to collect."""
    await CatalogCuration(db, clock).delete(slug)
