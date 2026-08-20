"""The life of a post-production task.

The clock is the interesting part. A task accumulates `elapsed_minutes` across
every stretch of work and stops accumulating whenever the operator is not on it —
so a batch left overnight on a paused task did not take fourteen hours, and the
pace figure the operator is judged on stays a measurement rather than an insult.

Everything here goes through `assert_transition`. There is no path that sets a
status directly, which is what makes the state machine in `policies` the whole
truth about how a task can move.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.postproduction import events as pp_events
from printorian.contexts.postproduction.models import Operation, Task, TaskStep
from printorian.contexts.postproduction.policies import (
    CURES,
    OperationKind,
    TaskStatus,
    assert_transition,
    norm_minutes,
    pace_percent,
    urgency_for,
)
from printorian.contexts.postproduction.schemas import (
    CreateTask,
    ReportDefect,
    StepView,
    TaskView,
)
from printorian.core.clock import Clock
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId

#: Prefix for the number an operator says out loud.
_NUMBER_PREFIX = "PP"


class PostProductionService:
    """Raising, working and closing finishing tasks."""

    def __init__(self, session: AsyncSession, clock: Clock, bus: EventBus) -> None:
        self._db = session
        self._clock = clock
        self._bus = bus

    # ------------------------------------------------------------- raising

    async def raise_task(self, data: CreateTask) -> TaskView:
        """Create the work a finished print implies.

        Called by the print-finished handler rather than by a person: the board's
        own footer promises that a task appears when a printer stops, and a queue
        that depended on somebody remembering to add to it is a queue that is
        wrong by the end of the first busy shift.
        """
        operation = await self._operation(data.kind)
        task = Task(
            number=await self._next_number(),
            order_id=data.order_id,
            model_name=data.model_name,
            material_code=data.material_code,
            colors=list(data.colors),
            printer_id=data.printer_id,
            operation_id=operation.id,
            quantity=data.quantity,
            due_at=data.due_at,
            norm_minutes=norm_minutes(operation.norm_minutes_per_unit, data.quantity),
            instruction_version=operation.instruction_version,
        )
        # The instruction is *copied*, not referenced: this is the record of what
        # somebody was told to do, and republishing version 4.3 mid-shift must not
        # rewrite the task an operator is halfway through.
        task.steps = [
            TaskStep(
                position=step.position,
                title=step.title,
                detail=step.detail,
                warning=step.warning,
                norm_minutes=step.norm_minutes,
            )
            for step in operation.steps
        ]
        self._db.add(task)
        await self._db.flush()

        await self._bus.publish(
            pp_events.TaskRaised(
                task_id=task.id,
                order_id=task.order_id,
                number=task.number,
                kind=operation.kind,
                quantity=task.quantity,
            )
        )
        return self._view(task, operation.kind)

    # ------------------------------------------------------------ the shift

    async def start(self, task_id: EntityId, operator_id: EntityId) -> TaskView:
        """Pick a task up. The clock starts here and nowhere else."""
        task = await self._task(task_id)
        assert_transition(task.status, TaskStatus.IN_PROGRESS)
        now = self._clock.now()

        was = task.status
        task.status = TaskStatus.IN_PROGRESS
        task.operator_id = operator_id
        task.running_since = now
        if task.started_at is None:
            task.started_at = now
        await self._settle(task, was)
        return await self._reload(task)

    async def pause(self, task_id: EntityId) -> TaskView:
        """Stop the clock without giving the task up."""
        task = await self._task(task_id)
        assert_transition(task.status, TaskStatus.PAUSED)
        was = task.status
        self._bank_time(task)
        task.status = TaskStatus.PAUSED
        await self._settle(task, was)
        return await self._reload(task)

    async def complete_step(self, task_id: EntityId, position: int) -> TaskView:
        """Tick one step off, recording what it actually cost.

        A step's fact is the time since the previous step was ticked, which is why
        the elapsed total is banked on every tick rather than only at the end: the
        two have to add up, and a per-step figure derived at the end from an
        average would make the instruction's norms unfalsifiable.
        """
        task = await self._task(task_id)
        if task.status is not TaskStatus.IN_PROGRESS:
            raise DomainRuleViolationError(
                "error.postproduction.not_in_progress", status=task.status.value
            )
        step = next((one for one in task.steps if one.position == position), None)
        if step is None:
            raise NotFoundError("error.postproduction.step_not_found", position=str(position))
        if step.done_at is not None:
            raise DomainRuleViolationError(
                "error.postproduction.step_already_done", position=str(position)
            )

        before = task.elapsed_minutes
        self._bank_time(task)
        task.running_since = self._clock.now()
        step.actual_minutes = (task.elapsed_minutes - before).quantize(Decimal("0.01"))
        step.done_at = self._clock.now()
        await self._db.flush()

        # The last step finishing moves the task on by itself. Making an operator
        # tick the final box *and* press a button is how batches sit finished on a
        # bench with the board still showing them as work in hand.
        if all(one.done_at is not None for one in task.steps):
            return await self.finish(task_id)
        return await self._reload(task)

    async def finish(self, task_id: EntityId) -> TaskView:
        """Work done. Either it goes to dry, or it goes to inspection."""
        task = await self._task(task_id)
        operation = await self._db.get(Operation, task.operation_id)
        if operation is None:  # pragma: no cover - RESTRICT protects this
            raise NotFoundError("error.postproduction.operation_not_found")

        now = self._clock.now()
        was = task.status
        self._bank_time(task)

        if operation.kind in CURES and operation.cure_minutes > 0:
            assert_transition(task.status, TaskStatus.CURING)
            task.status = TaskStatus.CURING
            task.cure_until = now + timedelta(minutes=operation.cure_minutes)
        else:
            assert_transition(task.status, TaskStatus.FOR_QC)
            task.status = TaskStatus.FOR_QC
            task.finished_at = now

        await self._settle(task, was)
        return await self._reload(task)

    async def cured(self, task_id: EntityId) -> TaskView:
        """The drying timer ran out. Called by the sweep, not by a person."""
        task = await self._task(task_id)
        assert_transition(task.status, TaskStatus.FOR_QC)
        was = task.status
        task.status = TaskStatus.FOR_QC
        task.finished_at = self._clock.now()
        await self._settle(task, was)
        return await self._reload(task)

    # ---------------------------------------------------------------- QC

    async def pass_qc(self, task_id: EntityId) -> TaskView:
        task = await self._task(task_id)
        assert_transition(task.status, TaskStatus.DONE)
        operation = await self._db.get(Operation, task.operation_id)
        was = task.status
        task.status = TaskStatus.DONE
        task.finished_at = task.finished_at or self._clock.now()
        await self._settle(task, was)
        if operation is not None:
            await self._bus.publish(
                pp_events.TaskCompleted(
                    task_id=task.id,
                    order_id=task.order_id,
                    number=task.number,
                    kind=operation.kind,
                )
            )
        return await self._reload(task)

    async def return_task(self, task_id: EntityId, data: ReportDefect) -> TaskView:
        """Send a batch back, with the reason attached.

        The steps are reopened and the attempt incremented rather than a second
        task being created, so the whole history stays on the batch the customer
        ordered — the same rule `production` applies to a failed print. The time
        already spent stays banked: a rework costs what it costs, and hiding the
        first attempt would make the norm look achievable when it was not.
        """
        task = await self._task(task_id)
        assert_transition(task.status, TaskStatus.RETURNED)
        was = task.status
        task.status = TaskStatus.RETURNED
        task.attempt += 1
        task.defect_code = data.defect_code
        task.defect_note = data.note
        task.finished_at = None
        for step in task.steps:
            step.done_at = None
            step.actual_minutes = None
        await self._settle(task, was)
        await self._bus.publish(
            pp_events.TaskReturned(
                task_id=task.id,
                order_id=task.order_id,
                number=task.number,
                defect_code=data.defect_code,
                attempt=task.attempt,
            )
        )
        return await self._reload(task)

    # ------------------------------------------------------------ internals

    async def _operation(self, kind: OperationKind) -> Operation:
        operation = await self._db.scalar(
            select(Operation)
            .where(Operation.kind == kind, Operation.is_active.is_(True))
            .options(selectinload(Operation.steps))
        )
        if operation is None:
            raise NotFoundError("error.postproduction.operation_not_found", kind=kind.value)
        return operation

    async def _task(self, task_id: EntityId) -> Task:
        task = await self._db.scalar(
            select(Task).where(Task.id == task_id).options(selectinload(Task.steps))
        )
        if task is None:
            raise NotFoundError("error.postproduction.task_not_found")
        return task

    def _bank_time(self, task: Task) -> None:
        """Move the running stretch into the accumulated total and stop the clock."""
        if task.running_since is None:
            return
        spent = (self._clock.now() - task.running_since) / timedelta(minutes=1)
        task.elapsed_minutes += Decimal(str(spent)).quantize(Decimal("0.01"))
        task.running_since = None

    async def _settle(self, task: Task, was: TaskStatus) -> None:
        await self._db.flush()
        if was is not task.status:
            await self._bus.publish(
                pp_events.TaskStatusChanged(
                    task_id=task.id,
                    order_id=task.order_id,
                    number=task.number,
                    from_status=was,
                    to_status=task.status,
                )
            )

    async def _reload(self, task: Task) -> TaskView:
        operation = await self._db.get(Operation, task.operation_id)
        kind = operation.kind if operation else OperationKind.SANDING
        return self._view(task, kind)

    def _view(self, task: Task, kind: OperationKind) -> TaskView:
        now = self._clock.now()
        # The clock is still running for a task in progress, so what the screen
        # shows must include the current stretch — otherwise elapsed time appears
        # frozen until the next step is ticked.
        live = task.elapsed_minutes
        if task.running_since is not None:
            live += Decimal(str((now - task.running_since) / timedelta(minutes=1))).quantize(
                Decimal("0.01")
            )
        to_due = (
            Decimal(str((task.due_at - now) / timedelta(minutes=1))).quantize(Decimal("0.1"))
            if task.due_at is not None
            else None
        )
        steps = [StepView.model_validate(step) for step in task.steps]
        remaining = sum(
            (step.norm_minutes for step in steps if step.done_at is None), start=Decimal(0)
        )

        return TaskView(
            id=task.id,
            number=task.number,
            status=task.status,
            kind=kind,
            order_id=task.order_id,
            model_name=task.model_name,
            material_code=task.material_code,
            colors=list(task.colors),
            printer_id=task.printer_id,
            quantity=task.quantity,
            due_at=task.due_at,
            urgency=urgency_for(to_due),
            minutes_to_due=to_due,
            norm_minutes=task.norm_minutes,
            elapsed_minutes=live.quantize(Decimal("0.01")),
            instruction_version=task.instruction_version,
            pace_percent=pace_percent(task.norm_minutes, live),
            projected_minutes=(live + remaining).quantize(Decimal("0.1")),
            operator_id=task.operator_id,
            started_at=task.started_at,
            finished_at=task.finished_at,
            cure_until=task.cure_until,
            attempt=task.attempt,
            defect_code=task.defect_code,
            defect_note=task.defect_note,
            steps=steps,
        )

    async def _next_number(self) -> str:
        """Sequential task numbers.

        Counted rather than sequenced: unlike an order number this is an internal
        label with no commercial meaning, a gap costs nothing, and a collision is
        caught by the unique constraint on the column.
        """
        highest = await self._db.scalar(select(func.count()).select_from(Task))
        return f"{_NUMBER_PREFIX}-{int(highest or 0) + 1:06d}"


__all__ = ["PostProductionService"]
