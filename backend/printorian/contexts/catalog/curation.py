"""The write side of the model library — what an editor does to it.

Separate from :mod:`browse`, which serves the shop window. The reader asks "what
can I order?"; the editor asks "what should we offer, and how is it described?".
Keeping them apart is what stops the public card growing an internal field because
it happened to be nearby.

**Three fields are never taken from the caller**, however the request spells them:

    size_class    derived from the asset's bounding box
    preview       projected from the asset's geometry
    search_text   folded from title, code and tags by a mapper event

Each is a fact about something else. A form that let an editor type "small" for a
220 mm tray would produce a catalogue whose size facet disagreed with the mesh it
filters, and nothing on the screen could reveal it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog.catalogue import (
    CatalogModel,
    CatalogModelMaterial,
    size_class_of,
)
from printorian.contexts.catalog.catalogue_schemas import (
    CreateCatalogModel,
    MaterialOffer,
    UpdateCatalogModel,
)
from printorian.contexts.catalog.models import ModelAsset
from printorian.contexts.catalog.preview import outline
from printorian.core.clock import Clock
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.ids import EntityId


class CatalogCuration:
    """Create and edit catalogue entries.

    Every method here is reached only through an endpoint holding
    `Permission.MANAGE_LIBRARY`. The check lives at the edge rather than in here
    because that is where the actor is, and a service that re-checked would be a
    second place for the rule to be wrong.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._db = session
        self._clock = clock

    async def create(self, data: CreateCatalogModel, *, geometry: bytes) -> CatalogModel:
        """Publish a new model, or refuse a slug that is taken.

        `geometry` is the asset's own bytes, read by the caller. They are needed
        here to project the card's drawing, and passing them in keeps this class
        free of the object store — the same separation `browse` has.
        """
        if await self._db.scalar(select(CatalogModel).where(CatalogModel.slug == data.slug)):
            raise ConflictError("error.catalog.slug_taken", details={"slug": data.slug})

        asset = await self._asset(data.model_asset_id)
        model = CatalogModel(
            slug=data.slug,
            code=data.code,
            title=data.title,
            summary=data.summary,
            category=data.category,
            # Derived, never taken from the request — see the module docstring.
            size_class=size_class_of(asset.width_mm, asset.depth_mm, asset.height_mm),
            difficulty=data.difficulty,
            strength=data.strength,
            accuracy=data.accuracy,
            speed=data.speed,
            supports=data.supports,
            postprocessing=data.postprocessing,
            author=data.author,
            multicolor=data.multicolor,
            tags=list(data.tags),
            model_asset_id=asset.id,
            license=data.license,
            version=data.version,
            preview=outline(geometry),
            is_published=data.is_published,
            published_at=self._clock.now() if data.is_published else None,
        )
        self._db.add(model)
        await self._db.flush()
        await self._set_materials(model, data.materials)
        await self._db.refresh(model, attribute_names=["materials", "asset"])
        return model

    async def update(
        self, slug: str, data: UpdateCatalogModel, *, geometry: bytes | None = None
    ) -> CatalogModel:
        """Apply the fields the editor actually changed.

        `exclude_unset` on the request means an absent field is "leave it alone",
        not "set it to null" — the difference between editing one column and
        blanking a description because the form did not send it.
        """
        model = await self._get(slug)
        changes = data.model_dump(exclude_unset=True)

        # The asset moving is what makes the derived fields stale, so they are
        # recomputed here rather than on every save.
        if "model_asset_id" in changes:
            asset = await self._asset(changes["model_asset_id"])
            model.model_asset_id = asset.id
            model.size_class = size_class_of(asset.width_mm, asset.depth_mm, asset.height_mm)
            if geometry is not None:
                model.preview = outline(geometry)

        if "materials" in changes:
            # `model_dump` flattened the offers into dicts on the way in.
            await self._set_materials(
                model, [MaterialOffer.model_validate(entry) for entry in changes.pop("materials")]
            )

        if "is_published" in changes:
            model.published_at = self._published_at(model, published=changes["is_published"])

        for field, value in changes.items():
            if field in {"model_asset_id", "materials"}:
                continue
            setattr(model, field, value)

        await self._db.flush()
        await self._db.refresh(model, attribute_names=["materials", "asset"])
        return model

    async def delete(self, slug: str) -> None:
        """Remove an entry.

        The `ModelAsset` is deliberately left behind. It is content-addressed and
        may be shared with an order that has already been placed; retention
        collects it when nothing references it, which is a decision that belongs to
        retention and not to an editor pressing delete.
        """
        model = await self._get(slug)
        await self._db.delete(model)
        await self._db.flush()

    def _published_at(self, model: CatalogModel, *, published: bool) -> datetime | None:
        """When this entry went public.

        Set on the first publish and kept thereafter: un-publishing and
        re-publishing a model does not make it new, and the catalogue sorts by
        this date.
        """
        if not published:
            return model.published_at
        return model.published_at or self._clock.now()

    async def _set_materials(self, model: CatalogModel, offers: list[MaterialOffer]) -> None:
        """Replace the material list wholesale.

        A diff would be fewer statements and more ways to be wrong; the list is
        never more than a handful of rows.

        Deleted by statement rather than by clearing `model.materials`: on a model
        that was just added the collection has never been loaded, and touching it
        triggers a lazy load from a context that cannot do IO. The statement works
        the same for a new row and an edited one.
        """
        await self._db.execute(
            delete(CatalogModelMaterial).where(CatalogModelMaterial.model_id == model.id)
        )
        seen: set[str] = set()
        for offer in offers:
            # De-duplicated by code, order preserved: an editor who lists PLA
            # twice meant it once.
            if offer.code in seen:
                continue
            seen.add(offer.code)
            self._db.add(
                CatalogModelMaterial(
                    model_id=model.id,
                    material_code=offer.code,
                    suitability=offer.suitability,
                    note=offer.note,
                    is_recommended=offer.is_recommended,
                )
            )
        await self._db.flush()

    async def _asset(self, asset_id: EntityId) -> ModelAsset:
        asset = await self._db.get(ModelAsset, asset_id)
        if asset is None:
            raise NotFoundError("error.catalog.model_not_found", details={"id": str(asset_id)})
        return asset

    async def _get(self, slug: str) -> CatalogModel:
        model = await self._db.scalar(select(CatalogModel).where(CatalogModel.slug == slug))
        if model is None:
            raise NotFoundError("error.model_not_found", details={"slug": slug})
        return model
