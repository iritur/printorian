"""The model library: take an upload, keep it, find it again.

The read side is what the whole prep chain hangs on. A quote that stores its asset
gives the order a `model_hash`; that hash is what `plate_key` is built from; a hit
there is the difference between an order dispatching by itself and an order waiting
for an engineer (ADR-0006).

Bytes go to the object store, measurements and references go to the database, and
neither knows about the other's failure modes — which is what lets the store become
S3-compatible later without touching anything here.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog.mesh import MeshAnalysis, analyse_stl
from printorian.contexts.catalog.models import ModelAsset, ModelFormat
from printorian.contexts.catalog.schemas import ModelAssetView
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import EntityId
from printorian.core.storage import ObjectStore, digest_of

#: Extensions the farm accepts, mapped to what they are.
FORMATS: dict[str, ModelFormat] = {
    "stl": ModelFormat.STL,
    "3mf": ModelFormat.THREE_MF,
}


def format_of(filename: str) -> ModelFormat:
    """What kind of file this is, from its name. Unknown extensions are `OTHER`."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return FORMATS.get(extension, ModelFormat.OTHER)


class ModelLibrary:
    """Uploaded geometry: stored once, measured once, found by content."""

    def __init__(self, session: AsyncSession, store: ObjectStore, clock: Clock) -> None:
        self._db = session
        self._store = store
        self._clock = clock

    # -- writing ---------------------------------------------------------

    async def ingest(
        self,
        data: bytes,
        *,
        filename: str,
        uploaded_by: EntityId | None = None,
        analysis: MeshAnalysis | None = None,
    ) -> ModelAssetView:
        """Store an upload and return the asset, reusing an identical one.

        **Idempotent on content.** The digest is the identity, so re-uploading a
        file the farm already holds writes nothing, re-parses nothing, and hands
        back the existing asset — which is also how a catalogue model and a
        customer's own upload of the same part come to share one prepared plate.

        ``analysis`` may be supplied by a caller that has already measured these
        exact bytes, so the configurator's quote path does not parse the same mesh
        twice. It is never accepted over the wire — only from the request that read
        the bytes it describes.
        """
        digest = digest_of(data)
        existing = await self._db.scalar(select(ModelAsset).where(ModelAsset.sha256 == digest))
        if existing is not None:
            # Seen before. Touch it so retention counts from this use rather than
            # the first: a model reprinted every month is never collected.
            existing.last_used_at = self._clock.now()
            await self._db.flush()
            return ModelAssetView.model_validate(existing)

        model_format = format_of(filename)
        measured = analysis if analysis is not None else _measure(data, model_format)

        # Bytes first, row second — deliberately, and it is the ordering the backup
        # guarantee depends on (RUNBOOK §7). A row naming an object that was never
        # written is a broken reference found at dispatch; an object with no row is
        # an orphan the next retention sweep collects.
        stored = await self._store.put(data, suffix=model_format.value)

        asset = ModelAsset(
            sha256=stored.digest,
            original_filename=filename[:300],
            format=model_format,
            size_bytes=stored.size_bytes,
            storage_path=stored.path,
            uploaded_by=uploaded_by,
            last_used_at=self._clock.now(),
            **_geometry_columns(measured),
        )
        self._db.add(asset)
        await self._db.flush()
        return ModelAssetView.model_validate(asset)

    async def touch(self, asset_id: EntityId) -> None:
        """Record that something used this asset, for retention's sake."""
        asset = await self._db.get(ModelAsset, asset_id)
        if asset is not None:
            asset.last_used_at = self._clock.now()
            await self._db.flush()

    # -- reading ---------------------------------------------------------

    async def get(self, asset_id: EntityId) -> ModelAssetView:
        asset = await self._db.get(ModelAsset, asset_id)
        if asset is None:
            raise NotFoundError("error.catalog.model_not_found", model_id=str(asset_id))
        return ModelAssetView.model_validate(asset)

    async def by_hash(self, sha256: str) -> ModelAssetView | None:
        asset = await self._db.scalar(select(ModelAsset).where(ModelAsset.sha256 == sha256))
        return ModelAssetView.model_validate(asset) if asset is not None else None

    async def content(self, asset_id: EntityId) -> tuple[bytes, str]:
        """The bytes, and the filename to offer them under.

        This is what the console hands an engineer to open in a slicer, so the
        original filename travels with the bytes — a directory of digests is
        unusable to a person.
        """
        view = await self.get(asset_id)
        return await self._store.get(view.sha256), view.original_filename

    # -- retention -------------------------------------------------------

    async def purge_unused(self, *, older_than: timedelta) -> int:
        """Delete assets nothing has used for ``older_than``. Returns the count.

        Retention exists from the first day rather than being retrofitted: every
        uploaded mesh kept forever fills the farm's disk, and a quote that was never
        ordered is the common case rather than the exception.

        **What protects a model in use is the foreign key, not a query here.**
        `order_lines.model_asset_id` is `RESTRICT`, so the database refuses to
        delete geometry an order still needs, and each candidate is therefore
        deleted in its own savepoint: a rejection means "still referenced", which is
        an answer rather than an error. Asking the question in SQL instead would
        mean this context knowing that `ordering` exists — the boundary
        ARCHITECTURE §3 draws — and would race with an order placed between the
        check and the delete.

        Rows go before objects. A crash between the two leaves an orphaned file for
        the next sweep to collect; the reverse leaves a row naming bytes that are
        gone, discovered at dispatch.
        """
        cutoff = self._clock.now() - older_than
        candidates = list(
            await self._db.scalars(select(ModelAsset).where(ModelAsset.last_used_at < cutoff))
        )

        collected: list[str] = []
        for asset in candidates:
            digest = asset.sha256
            try:
                async with self._db.begin_nested():
                    await self._db.delete(asset)
            except IntegrityError:
                # Referenced by an order line. Left alone, and left old, so it is
                # reconsidered on the next sweep once the order is done with it.
                continue
            collected.append(digest)

        for digest in collected:
            await self._store.delete(digest)
        return len(collected)


def _measure(data: bytes, model_format: ModelFormat) -> MeshAnalysis | None:
    """Analyse the upload, or return nothing when the format cannot be read.

    Only STL is measurable today. A 3MF is stored and served — an engineer can open
    it — but not measured, so `is_priceable` stays false for one and an order cannot
    be placed against geometry the system has never read. Storing it with zeroed
    measurements and letting it be priced anyway would be V1's mistake in a new
    place.
    """
    if model_format is not ModelFormat.STL:
        return None
    return analyse_stl(data)


def _geometry_columns(analysis: MeshAnalysis | None) -> dict[str, object]:
    """Column values for a measured mesh, or an honest blank for an unread one."""
    if analysis is None:
        return {"mesh": {"measured": False}}
    return {
        "triangle_count": analysis.triangle_count,
        "volume_cm3": analysis.volume.cubic_centimetres,
        "width_mm": analysis.bounding_box.x.millimetres,
        "depth_mm": analysis.bounding_box.y.millimetres,
        "height_mm": analysis.bounding_box.z.millimetres,
        "is_watertight": analysis.is_watertight,
        "mesh": mesh_to_dict(analysis),
    }


def mesh_to_dict(analysis: MeshAnalysis) -> dict[str, object]:
    """The analysis as plain data, for the column and for the API.

    One serializer used by both, for the same reason pricing has one: a wire format
    and a stored format that drift apart are how a quote stops being reproducible.
    """
    return {
        "measured": True,
        "triangle_count": analysis.triangle_count,
        "volume_cm3": str(analysis.volume.cubic_centimetres),
        "surface_area_mm2": str(analysis.surface_area_mm2),
        "bounding_box_mm": {
            "x": str(analysis.bounding_box.x.millimetres),
            "y": str(analysis.bounding_box.y.millimetres),
            "z": str(analysis.bounding_box.z.millimetres),
        },
        "is_watertight": analysis.is_watertight,
        "quality": analysis.quality.value,
        "warnings": [
            {"code": warning.code, "details": dict(warning.details)}
            for warning in analysis.warnings
        ],
    }


def assert_priceable(asset: ModelAssetView) -> None:
    """Refuse to price geometry with no defined volume.

    A mesh with holes has no volume, so any price quoted from it is a guess
    presented as a fact — the same rule the quote endpoint applies to raw bytes,
    applied to a stored asset.
    """
    if not asset.is_priceable:
        raise ValidationError(
            "error.catalog.mesh_not_priceable",
            watertight=str(asset.is_watertight),
            model_id=str(asset.id),
        )


__all__ = [
    "FORMATS",
    "ModelLibrary",
    "assert_priceable",
    "format_of",
    "mesh_to_dict",
]
