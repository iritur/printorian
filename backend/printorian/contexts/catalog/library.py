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

    async def find_unambiguous(
        self,
        *,
        model_hash: str,
        scale: Decimal,
        material_code: str,
    ) -> PreparedPlateView | None:
        """The one usable plate for this configuration, when there is exactly one.

        `find` above needs a printer profile, because the profile is part of
        ADR-0006's key: the same geometry sliced for a P1S and for an X1C are two
        different plates. An order does not carry one — the profile is the
        engineer's choice at the slicer, and nothing upstream of prep has made it.
        So the automatic intake path (`workers/intake.py`) cannot ask `find`
        anything, and this is what it asks instead.

        **It never picks.** Two valid plates for the same geometry and material
        mean the farm has sliced it for two profiles, and choosing between them is
        exactly the decision the engineer is there to make — an arbitrary one would
        send a plate sliced for one machine to another, which prints and produces
        rubbish. `None` therefore means both "nothing is cached" and "more than one
        thing is, and it is not this function's call"; both leave the job in the
        prep queue, which is where a person can see it.

        **That is not a guarantee that a plate reaches the machine it was sliced
        for, and this paragraph used to be read as one.** It only declines when
        there are *two* rows. With exactly one, the plate goes out and nothing
        downstream compares its `printer_profile` with the printer the planner
        picks — `JobRequirements` has no profile term and `Printer` has no profile
        column. Worse, the two-row case the argument above rests on cannot arise
        from the console at all: `POST /jobs/{id}/plate/file` defaults the profile
        to the literal `"default"` and the console sends none, so re-slicing a
        configuration for a second machine lands on the *same* key and `record`
        upserts over the first row instead of producing the ambiguity this function
        would have refused. `workers/plate_admission` lists the profile among the
        dimensions nothing checks, and says what closing it would cost.

        The key is still the authority. A row qualifies when *its own* profile and
        layout, fed back through `plate_key` with this configuration's geometry,
        material and scale, reproduce the key it is stored under — so the
        normalisation that decides whether "PLA " and "pla" are one material has
        one implementation here as everywhere else.
        """
        rows = await self._db.scalars(
            select(PreparedPlate).where(
                PreparedPlate.model_hash == model_hash,
                PreparedPlate.status == PlateStatus.VALID,
            )
        )
        matches = [
            plate
            for plate in rows
            if plate_key(
                model_hash=model_hash,
                scale=scale,
                material_code=material_code,
                printer_profile=plate.printer_profile,
                layout_hash=plate.layout_hash,
            )
            == plate.plate_key
        ]
        if len(matches) != 1:
            return None
        return PreparedPlateView.model_validate(matches[0])

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
        # Overwritten rather than merged, like every other field here: re-slicing a
        # configuration replaces the truth on the row, and a stale copy count left
        # behind from the previous layout is the one field on this row that decides
        # whether a later order attaches unattended.
        plate.copies = data.copies
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
