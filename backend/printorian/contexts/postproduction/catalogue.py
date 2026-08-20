"""Writing the instructions, and counting what the post uses up.

Split from `service.py` because it is a different job on a different clock: the
lifecycle is worked by an operator many times a shift, and this is edited by
whoever owns the process, rarely. Keeping them together also put the file over
the length gate, which is the gate doing exactly what it exists to do.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.postproduction.models import Consumable, InstructionStep, Operation
from printorian.contexts.postproduction.schemas import (
    ConsumableView,
    CreateOperation,
    CreateStep,
)
from printorian.core.ids import EntityId


class InstructionCatalogue:
    """The farm's own process documents."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def define_operation(self, data: CreateOperation, steps: list[CreateStep]) -> EntityId:
        """Create or replace one operation's norm and instruction.

        Replacing the steps wholesale rather than patching them: an instruction is
        a document, it is versioned as a document, and a half-applied edit is how
        step 4 ends up describing the tool step 3 already put away.
        """
        operation = await self._db.scalar(select(Operation).where(Operation.kind == data.kind))
        if operation is None:
            operation = Operation(kind=data.kind)
            self._db.add(operation)
        operation.norm_minutes_per_unit = data.norm_minutes_per_unit
        operation.cure_minutes = data.cure_minutes
        operation.instruction_version = data.instruction_version
        await self._db.flush()

        # The old steps are removed and *flushed* before the new ones are added.
        # Assigning the collection and flushing once looks equivalent and is not:
        # SQLAlchemy emits the inserts before the delete-orphans, so republishing
        # an instruction collides with `uq_instruction_step_position` on its own
        # previous version. Found by the test that republishes mid-shift.
        await self._db.execute(
            delete(InstructionStep).where(InstructionStep.operation_id == operation.id)
        )
        await self._db.flush()

        self._db.add_all(
            [
                InstructionStep(
                    operation_id=operation.id,
                    position=step.position,
                    title=step.title,
                    detail=step.detail,
                    warning=step.warning,
                    norm_minutes=step.norm_minutes,
                )
                for step in sorted(steps, key=lambda one: one.position)
            ]
        )
        await self._db.flush()
        # The in-memory collection still holds the deleted rows until it is
        # re-read, and `raise_task` copies from it.
        await self._db.refresh(operation, ["steps"])
        return operation.id

    async def consumables(self) -> list[ConsumableView]:
        rows = await self._db.scalars(
            select(Consumable).where(Consumable.is_active.is_(True)).order_by(Consumable.name)
        )
        return [ConsumableView.model_validate(row) for row in rows]


__all__ = ["InstructionCatalogue"]
