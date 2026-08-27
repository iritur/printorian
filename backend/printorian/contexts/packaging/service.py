"""The life of one parcel.

The clock works exactly as `postproduction`'s does — banked across interruptions,
so a parcel abandoned while the packer took a delivery did not take forty minutes
— and for the same reason: the pace figure a person is judged on has to stay a
measurement.

What is different is where the deadline comes from. A finishing task is urgent
because the customer was promised a date; a parcel is urgent because a van is
coming at 19:30. Everything in one pickup is due at the same instant, which is
why the board sorts on `cutoff_at` and the header counts down to it.

Consumption is recorded when the packer names what they used, not when the parcel
closes. A parcel abandoned halfway has still eaten the box.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.packaging import events as pk_events
from printorian.contexts.packaging.models import (
    PackInstruction,
    PackStep,
    PackTask,
    PackUse,
    Tara,
)
from printorian.contexts.packaging.policies import (
    Dims,
    HoldReason,
    PackStatus,
    assert_transition,
)
from printorian.contexts.packaging.schemas import (
    ChooseTara,
    CreatePackTask,
    HoldParcel,
    PackView,
    ReportDiscrepancy,
    Weigh,
)
from printorian.contexts.packaging.tara import enclosures, recommend
from printorian.contexts.packaging.views import total_norm, view_of
from printorian.core.clock import Clock
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId

#: Prefix for the number a packer says out loud.
_NUMBER_PREFIX = "PK"


class PackagingService:
    """Raising, packing and shipping parcels."""

    def __init__(self, session: AsyncSession, clock: Clock, bus: EventBus) -> None:
        self._db = session
        self._clock = clock
        self._bus = bus

    # ------------------------------------------------------------- raising

    async def raise_parcel(self, data: CreatePackTask) -> PackView:
        """Create the parcel an inspected order implies.

        Called by the sweep rather than by a person, for the reason the board's
        footer states: a parcel appears when the last finishing task passes QC,
        and a queue that depended on somebody remembering would be wrong by the
        end of the first busy shift.
        """
        instruction = await self._instruction()
        task = PackTask(
            number=await self._next_number(),
            order_id=data.order_id,
            delivery_method=data.delivery_method,
            carrier_code=data.carrier_code,
            cutoff_at=data.cutoff_at,
            items=data.items,
            estimated_grams=data.estimated_grams,
            length_mm=data.length_mm,
            width_mm=data.width_mm,
            height_mm=data.height_mm,
            wrap_required=data.wrap_required,
            instruction_version=instruction.version if instruction else "",
            norm_minutes=total_norm(instruction),
        )
        # The instruction is copied, not referenced: this is the record of what
        # somebody was told to do, and republishing 2.2 mid-shift must not rewrite
        # the parcel a packer is halfway through.
        if instruction is not None:
            task.steps = [
                PackStep(
                    position=step.position,
                    title=step.title,
                    detail=step.detail,
                    warning=step.warning,
                    norm_minutes=step.norm_minutes,
                )
                for step in instruction.steps
            ]
        self._db.add(task)
        await self._db.flush()

        await self._bus.publish(
            pk_events.ParcelRaised(
                task_id=task.id, order_id=task.order_id, number=task.number, items=task.items
            )
        )
        return await self._view(task)

    # ---------------------------------------------------------------- shift

    async def start(self, task_id: EntityId, operator_id: EntityId) -> PackView:
        """Pick a parcel up. The clock starts here and nowhere else."""
        task = await self._task(task_id)
        assert_transition(task.status, PackStatus.PACKING)
        now = self._clock.now()

        was = task.status
        task.status = PackStatus.PACKING
        task.operator_id = operator_id
        task.running_since = now
        if task.started_at is None:
            task.started_at = now
        await self._settle(task, was)
        return await self._view(task)

    async def tick(self, task_id: EntityId, position: int) -> PackView:
        """Tick one step off, recording what it actually cost.

        A step's fact is the time since the previous tick, which is why the total
        is banked on every one rather than only at the end: the parts have to add
        up to the whole, and per-step figures derived at the end from an average
        would make the instruction's norms unfalsifiable.
        """
        task = await self._task(task_id)
        if task.status is not PackStatus.PACKING:
            raise DomainRuleViolationError("error.packaging.not_packing", status=task.status.value)
        step = next((one for one in task.steps if one.position == position), None)
        if step is None:
            raise NotFoundError("error.packaging.step_not_found", position=str(position))
        if step.done_at is not None:
            raise DomainRuleViolationError(
                "error.packaging.step_already_done", position=str(position)
            )

        before = task.elapsed_minutes
        self._bank_time(task)
        now = self._clock.now()
        task.running_since = now
        step.actual_minutes = (task.elapsed_minutes - before).quantize(Decimal("0.01"))
        step.done_at = now
        await self._db.flush()

        # The last step closes the parcel by itself. Making a packer tick the
        # final box *and* press a button is how sealed parcels sit on a bench
        # with the board still showing them as work in hand.
        if all(one.done_at is not None for one in task.steps):
            return await self.ready(task_id)
        return await self._view(task)

    async def choose_tara(self, task_id: EntityId, data: ChooseTara) -> PackView:
        """Record what went into the parcel, and what it cost.

        Written when the packer names it rather than when the parcel closes: a
        parcel abandoned halfway has still consumed the box, and a ledger that
        only counted finished work would understate the month.
        """
        task = await self._task(task_id)
        if task.status.is_terminal:
            raise DomainRuleViolationError("error.packaging.closed", status=task.status.value)

        wanted: dict[EntityId, Decimal] = {data.tara_id: Decimal(1)}
        for tara_id, quantity in data.extras.items():
            if quantity > 0:
                wanted[tara_id] = wanted.get(tara_id, Decimal(0)) + quantity

        # Replaced wholesale rather than added to: choosing a different box is a
        # correction, and a ledger that accumulated both would bill the parcel for
        # two enclosures it never had.
        #
        # Issued as a statement rather than by walking `task.used`. The rows are
        # added with `session.add` rather than through the relationship, so an
        # already-loaded collection does not see them — and a second call would
        # find nothing to delete and leave the parcel billed for both boxes. Found
        # by the test that changes its mind about the box.
        await self._db.execute(delete(PackUse).where(PackUse.task_id == task.id))
        await self._db.flush()

        cost = Decimal(0)
        for tara_id, quantity in wanted.items():
            tara = await self._db.get(Tara, tara_id)
            if tara is None:
                raise NotFoundError("error.packaging.tara_not_found")
            self._db.add(
                PackUse(task_id=task.id, tara_id=tara.id, quantity=quantity, unit_price=tara.price)
            )
            # Stock moves here too. The alternative — decrementing on close — has
            # the post's shelf disagreeing with the post's screen for the length
            # of every parcel, which is when somebody is looking at both.
            tara.stock = max(Decimal(0), tara.stock - quantity)
            cost += tara.price * quantity

        task.tara_id = data.tara_id
        task.packaging_cost = cost.quantize(Decimal("0.01"))
        await self._db.flush()
        return await self._view(task)

    async def weigh(self, task_id: EntityId, data: Weigh) -> PackView:
        """What the scales said. The fact beside the estimate, never replacing it."""
        task = await self._task(task_id)
        if task.status.is_terminal:
            raise DomainRuleViolationError("error.packaging.closed", status=task.status.value)
        task.weight_grams = data.weight_grams
        await self._db.flush()
        return await self._view(task)

    async def ready(self, task_id: EntityId) -> PackView:
        """Sealed, weighed, labelled. Waiting for the van."""
        task = await self._task(task_id)
        assert_transition(task.status, PackStatus.READY)
        was = task.status
        self._bank_time(task)
        task.status = PackStatus.READY
        task.finished_at = self._clock.now()
        await self._settle(task, was)
        return await self._view(task)

    async def ship(self, task_id: EntityId) -> PackView:
        """Handed to the carrier."""
        task = await self._task(task_id)
        assert_transition(task.status, PackStatus.SHIPPED)
        was = task.status
        task.status = PackStatus.SHIPPED
        task.shipped_at = self._clock.now()
        await self._settle(task, was)
        await self._bus.publish(
            pk_events.ParcelShipped(
                task_id=task.id,
                order_id=task.order_id,
                number=task.number,
                carrier_code=task.carrier_code,
                weight_grams=task.weight_grams or task.estimated_grams,
            )
        )
        return await self._view(task)

    # ----------------------------------------------------------- blocked

    async def hold(self, task_id: EntityId, data: HoldParcel) -> PackView:
        """Park a parcel on somebody else's problem, with the reason attached."""
        task = await self._task(task_id)
        assert_transition(task.status, PackStatus.HELD)
        was = task.status
        self._bank_time(task)
        task.status = PackStatus.HELD
        task.hold_reason = data.reason
        task.discrepancy_note = data.note or task.discrepancy_note
        await self._settle(task, was)
        await self._bus.publish(
            pk_events.ParcelHeld(
                task_id=task.id,
                order_id=task.order_id,
                number=task.number,
                reason=data.reason,
            )
        )
        return await self._view(task)

    async def release(self, task_id: EntityId) -> PackView:
        """Whatever blocked it is cleared. Back into the queue, not into a packer's hands."""
        task = await self._task(task_id)
        assert_transition(task.status, PackStatus.CHECKED)
        was = task.status
        task.status = PackStatus.CHECKED
        task.hold_reason = None
        await self._settle(task, was)
        return await self._view(task)

    async def report_discrepancy(self, task_id: EntityId, data: ReportDiscrepancy) -> PackView:
        """The count disagreed with the order. Stops the parcel where it stands.

        Held rather than merely flagged: a short parcel that stayed packable is a
        short parcel that ships. The code is the input to the «недовложений»
        figure the post is judged on, which is exactly why it is not optional.
        """
        task = await self._task(task_id)
        assert_transition(task.status, PackStatus.HELD)
        was = task.status
        self._bank_time(task)
        task.status = PackStatus.HELD
        task.hold_reason = HoldReason.ITEM_MISSING
        task.discrepancy_code = data.discrepancy_code
        task.discrepancy_note = data.note
        task.discrepancy_at = self._clock.now()
        await self._settle(task, was)
        await self._bus.publish(
            pk_events.DiscrepancyFound(
                task_id=task.id,
                order_id=task.order_id,
                number=task.number,
                discrepancy_code=data.discrepancy_code,
            )
        )
        return await self._view(task)

    # ------------------------------------------------------------ internals

    async def _instruction(self) -> PackInstruction | None:
        found: PackInstruction | None = await self._db.scalar(
            select(PackInstruction)
            .where(PackInstruction.is_active.is_(True))
            .options(selectinload(PackInstruction.steps))
            # Same order as `InstructionCatalogue.active`, tiebreak included —
            # two rows that disagreed about which version is current would be
            # worse than either answer.
            .order_by(PackInstruction.created_at.desc(), PackInstruction.id.desc())
            .limit(1)
        )
        return found

    async def _task(self, task_id: EntityId) -> PackTask:
        task = await self._db.scalar(
            select(PackTask)
            .where(PackTask.id == task_id)
            # Steps only. `used` is written and re-read by statement, and eagerly
            # loading a collection this method does not touch would just be a
            # second stale copy of it.
            .options(selectinload(PackTask.steps))
        )
        if task is None:
            raise NotFoundError("error.packaging.task_not_found")
        return task

    def _bank_time(self, task: PackTask) -> None:
        """Move the running stretch into the total and stop the clock."""
        if task.running_since is None:
            return
        spent = (self._clock.now() - task.running_since) / timedelta(minutes=1)
        task.elapsed_minutes += Decimal(str(spent)).quantize(Decimal("0.01"))
        task.running_since = None

    async def _settle(self, task: PackTask, was: PackStatus) -> None:
        await self._db.flush()
        if was is not task.status:
            await self._bus.publish(
                pk_events.ParcelStatusChanged(
                    task_id=task.id,
                    order_id=task.order_id,
                    number=task.number,
                    from_status=was,
                    to_status=task.status,
                )
            )

    async def _view(self, task: PackTask) -> PackView:
        suggested = recommend(
            await enclosures(self._db), Dims(task.length_mm, task.width_mm, task.height_mm)
        )
        chosen = await self._db.get(Tara, task.tara_id) if task.tara_id else None
        return view_of(task, now=self._clock.now(), suggested=suggested, chosen=chosen)

    async def _next_number(self) -> str:
        """Sequential parcel numbers.

        Counted rather than sequenced: unlike an order number this is an internal
        label with no commercial meaning, a gap costs nothing, and a collision is
        caught by the unique constraint on the column.
        """
        highest = await self._db.scalar(select(func.count()).select_from(PackTask))
        return f"{_NUMBER_PREFIX}-{int(highest or 0) + 1:06d}"


__all__ = ["PackagingService"]
