"""Writing the packing instruction, and stocking the tara.

Split from `service.py` for the reason `postproduction`'s catalogue is: a
different job on a different clock. The lifecycle is worked many times a shift by
a packer; this is edited rarely, by whoever owns the process.

**Publishing is versioned, never edited in place.** Parcels copy their steps at
creation, so a republish changes what the *next* parcel is worked to and leaves
every parcel already open measured against what its packer was actually told.
That is also why the previous version is deactivated rather than deleted — the
norms behind last month's pace figures have to stay reconstructable.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.packaging.models import PackInstruction, PackInstructionStep, Tara
from printorian.contexts.packaging.schemas import CreateTara, PublishInstruction, TaraRow
from printorian.core.errors import DomainRuleViolationError
from printorian.core.ids import EntityId


class PackingCatalogue:
    """The farm's own packing document, and the shelf it draws from."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def publish(self, data: PublishInstruction) -> EntityId:
        """Put a new version of the instruction into service.

        A version already published is refused rather than overwritten. Silently
        replacing 2.1 would leave every parcel worked to the old 2.1 claiming a
        version whose steps no longer exist, and the whole point of copying the
        steps onto the task is that such a claim stays true.
        """
        existing = await self._db.scalar(
            select(PackInstruction).where(PackInstruction.version == data.version)
        )
        if existing is not None:
            raise DomainRuleViolationError("error.packaging.version_exists", version=data.version)

        await self._db.execute(update(PackInstruction).values(is_active=False))
        instruction = PackInstruction(version=data.version, reason=data.reason, is_active=True)
        self._db.add(instruction)
        await self._db.flush()

        self._db.add_all(
            [
                PackInstructionStep(
                    instruction_id=instruction.id,
                    position=step.position,
                    title=step.title,
                    detail=step.detail,
                    warning=step.warning,
                    norm_minutes=step.norm_minutes,
                )
                for step in sorted(data.steps, key=lambda one: one.position)
            ]
        )
        await self._db.flush()
        await self._db.refresh(instruction, ["steps"])
        return instruction.id

    async def active(self) -> PackInstruction | None:
        """The version new parcels are raised against.

        `id` as well as time because this picks *one* row and two versions
        published together tie on `created_at`, which would let the farm pack
        against a different instruction from one parcel to the next
        (`core.pagination`). `PackingService._instruction` sorts identically.
        """
        found: PackInstruction | None = await self._db.scalar(
            select(PackInstruction)
            .where(PackInstruction.is_active.is_(True))
            .order_by(PackInstruction.created_at.desc(), PackInstruction.id.desc())
            .limit(1)
        )
        return found

    async def stock_tara(self, data: CreateTara) -> TaraRow:
        """Add a packing item, or restate one already on the shelf.

        Keyed by code rather than by id, because this is how a new box arrives:
        somebody types the code from the supplier's invoice, and asking them to
        first find out whether the farm already knows it is asking them to do the
        lookup the database can do.
        """
        tara = await self._db.scalar(select(Tara).where(Tara.code == data.code))
        if tara is None:
            tara = Tara(code=data.code, kind=data.kind)
            self._db.add(tara)
        tara.name = data.name
        tara.kind = data.kind
        tara.unit = data.unit
        tara.inner_length_mm = data.inner_length_mm
        tara.inner_width_mm = data.inner_width_mm
        tara.inner_height_mm = data.inner_height_mm
        tara.price = data.price
        tara.stock = data.stock
        tara.reorder_at = data.reorder_at
        tara.is_active = True
        await self._db.flush()
        return TaraRow.model_validate(tara)

    async def retire_tara(self, tara_id: EntityId) -> None:
        """Take a box out of the catalogue without erasing what shipped in it.

        Deactivated, never deleted: `PackUse` holds it under `RESTRICT` precisely
        so last month's parcel costs cannot be rewritten by tidying the shelf.
        """
        tara = await self._db.get(Tara, tara_id)
        if tara is not None:
            tara.is_active = False
            await self._db.flush()


__all__ = ["PackingCatalogue"]
