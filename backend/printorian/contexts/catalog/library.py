"""The plate library: find a cached plate, or record a new one.

The read side is what makes ADR-0006 work — a hit here is the difference between
an order dispatching by itself and an order waiting for an engineer.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog.models import PlateStatus, PreparedPlate
from printorian.contexts.catalog.plate_key import plate_key
from printorian.contexts.catalog.schemas import PreparedPlateView, RecordPlate
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError
from printorian.core.ids import EntityId


class PlateLibrary:
    """Cached slicer output, keyed by configuration."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._db = session
        self._clock = clock

    async def find(
        self,
        *,
        model_hash: str,
        scale: Decimal,
        material_code: str,
        printer_profile: str,
        layout_hash: str = "",
    ) -> PreparedPlateView | None:
        """A usable plate for this configuration, or nothing.

        Only `VALID` plates are returned. A stale or rejected one is kept for the
        history of jobs that used it, but handing it to a new job would print from
        a profile somebody has already said is wrong.
        """
        key = plate_key(
            model_hash=model_hash,
            scale=scale,
            material_code=material_code,
            printer_profile=printer_profile,
            layout_hash=layout_hash,
        )
        plate = await self._db.scalar(
            select(PreparedPlate).where(
                PreparedPlate.plate_key == key,
                PreparedPlate.status == PlateStatus.VALID,
            )
        )
        return PreparedPlateView.model_validate(plate) if plate else None

    async def record(self, data: RecordPlate) -> PreparedPlateView:
        """Store what an engineer sliced.

        Re-slicing a configuration that already has a plate *replaces* the truth on
        the existing row rather than adding a second one: the key is unique, and
        two rows for one configuration would make "which plate does this order
        use" a coin toss.
        """
        key = plate_key(
            model_hash=data.model_hash,
            scale=data.scale,
            material_code=data.material_code,
            printer_profile=data.printer_profile,
            layout_hash=data.layout_hash,
        )
        plate = await self._db.scalar(select(PreparedPlate).where(PreparedPlate.plate_key == key))
        if plate is None:
            plate = PreparedPlate(plate_key=key)
            self._db.add(plate)

        plate.status = PlateStatus.VALID
        plate.model_hash = data.model_hash
        plate.model_name = data.model_name
        plate.scale = data.scale
        plate.material_code = data.material_code
        plate.printer_profile = data.printer_profile
        plate.layout_hash = data.layout_hash
        plate.print_minutes = data.print_minutes
        plate.filament_grams = {str(k): str(v) for k, v in data.filament_grams.items()}
        plate.layer_count = data.layer_count
        plate.filename = data.filename
        plate.content_sha256 = data.content_sha256
        plate.storage_path = data.storage_path
        plate.size_bytes = data.size_bytes
        plate.model_asset_id = data.model_asset_id
        plate.sliced_by = data.sliced_by
        plate.sliced_at = self._clock.now()
        plate.slicer_name = data.slicer_name
        plate.slicer_version = data.slicer_version
        plate.profile_version = data.profile_version

        await self._db.flush()
        return PreparedPlateView.model_validate(plate)

    async def get(self, plate_id: EntityId) -> PreparedPlateView:
        plate = await self._db.get(PreparedPlate, plate_id)
        if plate is None:
            raise NotFoundError("error.catalog.plate_not_found", plate_id=str(plate_id))
        return PreparedPlateView.model_validate(plate)

    async def invalidate(self, plate_id: EntityId, *, status: PlateStatus) -> PreparedPlateView:
        """Mark a plate unusable without deleting it.

        Deleting would break the explanation of every job that already printed from
        it. ADR-0006's stale case: the model or the profile moved on.
        """
        plate = await self._db.get(PreparedPlate, plate_id)
        if plate is None:
            raise NotFoundError("error.catalog.plate_not_found", plate_id=str(plate_id))
        plate.status = status
        await self._db.flush()
        return PreparedPlateView.model_validate(plate)

    async def invalidate_profile(self, printer_profile: str) -> int:
        """Retire every plate sliced with a profile that has changed.

        A profile version bump makes its plates untrustworthy in one stroke — which
        is exactly why the profile version is stored on each row.
        """
        plates = list(
            await self._db.scalars(
                select(PreparedPlate).where(
                    PreparedPlate.printer_profile == printer_profile,
                    PreparedPlate.status == PlateStatus.VALID,
                )
            )
        )
        for plate in plates:
            plate.status = PlateStatus.STALE
        await self._db.flush()
        return len(plates)
