"""Persistent models for post-production.

Four tables and one deliberate refusal.

The refusal is `Consumable`. Sandpaper, primer and nitrile gloves are stocked
items with a quantity and a threshold, so putting them in `inventory` beside the
filament is the obvious move — and it is wrong. A `MaterialSpec` carries a
density and a non-nullable sell-price-per-gram because the pricing engine reads
both; a box of gloves has neither, and forcing it in would push nulls into the
one function ADR-0002 keeps pure. They are counted here, in the context that
consumes them, and they buy through purchasing like everything else when that
lands.

The instruction is **versioned and copied onto the task**. An operator working to
version 4.2 must still be measured against 4.2 after somebody publishes 4.3
mid-shift, and a norm that changed retroactively is a norm nobody trusts.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.postproduction.policies import OperationKind, TaskStatus
from printorian.core.db import Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class Operation(Entity):
    """One kind of finishing work, with the norm it is measured against.

    The norm lives on the row rather than in code because it is the number the
    farm will spend the next year arguing with — the analytics panel exists to
    show fact against it, and a norm that needed a deploy to correct would simply
    go on being wrong.
    """

    __tablename__ = "postproduction_operations"
    __table_args__ = (
        UniqueConstraint("kind", name="uq_postproduction_operations_kind"),
        CheckConstraint("norm_minutes_per_unit > 0", name="norm_positive"),
        CheckConstraint("cure_minutes >= 0", name="cure_non_negative"),
    )

    kind: Mapped[OperationKind] = mapped_column(enum_column(OperationKind), nullable=False)
    #: Minutes for one part. Multiplied by the batch — see `policies.norm_minutes`,
    #: which is honest about how crude that is.
    norm_minutes_per_unit: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal(5)
    )
    #: How long the part then stands drying. Zero for operations that do not cure.
    cure_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The instruction's version, e.g. "4.2". Copied onto every task started under
    #: it, so a mid-shift republish cannot change what an operator was working to.
    instruction_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    steps: Mapped[list[InstructionStep]] = relationship(
        back_populates="operation",
        cascade="all, delete-orphan",
        order_by="InstructionStep.position",
    )


class InstructionStep(Entity):
    """One numbered step, carrying its own time norm.

    The norm-per-step is the load-bearing idea of the whole screen: it is what
    turns a target into a gauge an operator reads before they start.
    """

    __tablename__ = "postproduction_instruction_steps"
    __table_args__ = (
        UniqueConstraint("operation_id", "position", name="uq_instruction_step_position"),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint("norm_minutes >= 0", name="step_norm_non_negative"),
    )

    operation_id: Mapped[EntityId] = mapped_column(
        ForeignKey("postproduction_operations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Prose, in the farm's own language. Deliberately *not* a message code: this
    #: is written by the shop for the shop, and ADR-0012 governs what the backend
    #: *emits about itself*, not content the farm authors.
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: The block that names the thing which actually causes returns.
    warning: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    norm_minutes: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal(1))

    operation: Mapped[Operation] = relationship(back_populates="steps")


class Task(Entity):
    """One batch of one operation, for one order line."""

    __tablename__ = "postproduction_tasks"
    __table_args__ = (
        # The board's query: everything not finished, oldest promise first.
        Index("ix_postproduction_tasks_status_due", "status", "due_at"),
        Index("ix_postproduction_tasks_order_id", "order_id"),
        Index("ix_postproduction_tasks_operator_id", "operator_id"),
        # PostgreSQL does not index a foreign key for you, and the board reads
        # through this one on every refresh to name each card's operation.
        Index("ix_postproduction_tasks_operation_id", "operation_id"),
        Index("ix_postproduction_tasks_printer_id", "printer_id"),
        UniqueConstraint("number", name="uq_postproduction_tasks_number"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("elapsed_minutes >= 0", name="elapsed_non_negative"),
        CheckConstraint("norm_minutes >= 0", name="task_norm_non_negative"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
    )

    #: The number an operator says out loud, e.g. `PP-2147`.
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The order this batch belongs to.
    #:
    #: A real foreign key, even though `ordering` is another context. The import
    #: boundary is about Python modules, not about what the database may check —
    #: there is one database and one domain model (rule 1), and `print_jobs`
    #: references `orders` exactly like this. ``CASCADE`` for the same reason it
    #: does: an order that no longer exists has no finishing work.
    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    #: What was printed, denormalised so a task stays readable after retention has
    #: collected the mesh it came from.
    model_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    material_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    colors: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)
    #: Which machine it came off, for the "снято с принтера" line. ``SET NULL``:
    #: retiring a printer must not destroy the record of what it made.
    printer_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )

    operation_id: Mapped[EntityId] = mapped_column(
        ForeignKey("postproduction_operations.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        enum_column(TaskStatus), nullable=False, default=TaskStatus.WAITING
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    #: The order's promise, copied at creation. The board's urgency is derived from
    #: it, and a task whose order has no promise simply sorts last.
    due_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The norm for *this* batch at *this* size, frozen when the task was made.
    norm_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    #: Which instruction version this task is being worked to.
    instruction_version: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    operator_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Time actually spent, accumulated across pauses. Kept as a running total
    #: rather than derived from `started_at`, because a task that was paused for
    #: lunch did not take four hours.
    elapsed_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    #: When the current stretch of work began. Null whenever the clock is stopped.
    running_since: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: When the drying ends, for tasks that cure.
    cure_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: How many times this batch has been through. A QC return increments it.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Why it came back. A code, never prose (ADR-0012).
    defect_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: What the inspector added in their own words, when a code is not enough.
    defect_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    operation: Mapped[Operation] = relationship()
    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskStep.position",
    )


class TaskStep(Entity):
    """One step of one task, and how long it actually took.

    A copy of the instruction step rather than a pointer to it, for the same
    reason the version is copied: this is the record of what somebody was told to
    do and what it cost them, and it must not change when the instruction does.
    """

    __tablename__ = "postproduction_task_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "position", name="uq_task_step_position"),
        CheckConstraint("position >= 1", name="task_step_position_positive"),
        CheckConstraint("norm_minutes >= 0", name="task_step_norm_non_negative"),
        CheckConstraint(
            "actual_minutes IS NULL OR actual_minutes >= 0", name="task_step_actual_non_negative"
        ),
    )

    task_id: Mapped[EntityId] = mapped_column(
        ForeignKey("postproduction_tasks.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    warning: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    norm_minutes: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal(1))
    #: Null until the step is ticked. The fact half of the fact-versus-norm pair.
    actual_minutes: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    task: Mapped[Task] = relationship(back_populates="steps")


class Consumable(Entity):
    """Something the post uses up. See the module docstring for why it is here."""

    __tablename__ = "postproduction_consumables"
    __table_args__ = (
        UniqueConstraint("code", name="uq_postproduction_consumables_code"),
        CheckConstraint("remaining >= 0", name="consumable_remaining_non_negative"),
        CheckConstraint("reorder_at >= 0", name="consumable_reorder_non_negative"),
    )

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: A unit *code* — `sheet`, `can`, `pair`, `litre` — which the client renders.
    #: Sandpaper is counted in sheets and isopropanol in litres, and a single
    #: numeric column with no unit is how a stock figure becomes meaningless.
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="piece")
    remaining: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    #: The level at which the panel starts warning. Zero disables it.
    reorder_at: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


__all__ = ["Consumable", "InstructionStep", "Operation", "Task", "TaskStep"]
