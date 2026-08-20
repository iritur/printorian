"""Persistent models for the packing post.

Six tables, and two of the shapes are borrowed on purpose. The instruction and
its per-step norms are the same idea `postproduction` uses and for the same
reason — a norm is a gauge an operator reads *before* they start — but the rows
are this post's own, because packing is one operation with one instruction and
finishing is six operations with six.

**One parcel per order.** `order_id` is unique here, which is the difference from
a finishing task: the post's unit is the box the customer opens, not a batch of
one operation. Splitting an order across two parcels is a real thing that will
eventually need modelling, and doing it now — before the farm has ever done it —
would buy a nullable parcel index and a class of "which half is this" bugs for
no present benefit.

**Tara is stocked here, not in `inventory`.** The same refusal `postproduction`
makes about sandpaper: a `MaterialSpec` carries a density and a sell price per
gram because the pricing engine reads both, and a cardboard box has neither.
What a box does have is *inner dimensions*, which nothing in `inventory` models
and which are the whole basis of choosing one.
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

from printorian.contexts.packaging.policies import HoldReason, PackStatus, TaraKind
from printorian.core.db import Entity, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class Tara(Entity):
    """One stocked packing item: a box, a bag, a roll of film, a sack of filler."""

    __tablename__ = "packaging_tara"
    __table_args__ = (
        UniqueConstraint("code", name="uq_packaging_tara_code"),
        CheckConstraint("price >= 0", name="tara_price_non_negative"),
        CheckConstraint("stock >= 0", name="tara_stock_non_negative"),
        CheckConstraint("reorder_at >= 0", name="tara_reorder_non_negative"),
        CheckConstraint(
            "inner_length_mm IS NULL OR inner_length_mm > 0", name="tara_length_positive"
        ),
    )

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[TaraKind] = mapped_column(enum_column(TaraKind), nullable=False)

    #: Inner dimensions, for the kinds that enclose something. Null for a roll or
    #: a sack, which is why these are nullable rather than zero: a box with no
    #: measured inside would otherwise silently become one that fits nothing.
    inner_length_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 1), nullable=True)
    inner_width_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 1), nullable=True)
    inner_height_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 1), nullable=True)

    #: What one costs the farm. The price the packing cost is summed from — and
    #: the reason choosing a size larger "to be safe" is visible in a total.
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal(0))
    #: A unit *code* — `piece`, `roll`, `kg` — rendered by the client. Film is
    #: counted in rolls and filler in kilos; one bare number with no unit is how a
    #: stock figure stops meaning anything.
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="piece")
    stock: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal(0))
    #: The level the panel starts warning at. Zero disables it.
    reorder_at: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal(0))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PackInstruction(Entity):
    """One published version of how to pack a parcel."""

    __tablename__ = "packaging_instructions"
    __table_args__ = (UniqueConstraint("version", name="uq_packaging_instructions_version"),)

    #: e.g. "2.1". Copied onto every task started under it.
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Why it changed. Shown in the panel's footer, because an instruction that
    #: gained a mandatory step after two damages teaches more than the step alone.
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    steps: Mapped[list[PackInstructionStep]] = relationship(
        back_populates="instruction",
        cascade="all, delete-orphan",
        order_by="PackInstructionStep.position",
    )


class PackInstructionStep(Entity):
    """One numbered step of the published instruction, with its own time norm."""

    __tablename__ = "packaging_instruction_steps"
    __table_args__ = (
        UniqueConstraint("instruction_id", "position", name="uq_packaging_step_position"),
        CheckConstraint("position >= 1", name="packaging_step_position_positive"),
        CheckConstraint("norm_minutes >= 0", name="packaging_step_norm_non_negative"),
    )

    instruction_id: Mapped[EntityId] = mapped_column(
        ForeignKey("packaging_instructions.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Prose the shop writes for the shop. ADR-0012 governs what the backend emits
    #: about itself, not content the farm authors.
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: The block naming the thing that actually causes damage in transit.
    warning: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    norm_minutes: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal(1))

    instruction: Mapped[PackInstruction] = relationship(back_populates="steps")


class PackTask(Entity):
    """One order's parcel, from inspected to handed over."""

    __tablename__ = "packaging_tasks"
    __table_args__ = (
        # The board's query: everything open, soonest van first.
        Index("ix_packaging_tasks_status_cutoff", "status", "cutoff_at"),
        Index("ix_packaging_tasks_operator_id", "operator_id"),
        Index("ix_packaging_tasks_tara_id", "tara_id"),
        UniqueConstraint("number", name="uq_packaging_tasks_number"),
        # One parcel per order — see the module docstring.
        UniqueConstraint("order_id", name="uq_packaging_tasks_order_id"),
        CheckConstraint("items >= 0", name="packaging_items_non_negative"),
        CheckConstraint("elapsed_minutes >= 0", name="packaging_elapsed_non_negative"),
        CheckConstraint("norm_minutes >= 0", name="packaging_norm_non_negative"),
        CheckConstraint("packaging_cost >= 0", name="packaging_cost_non_negative"),
        CheckConstraint(
            "weight_grams IS NULL OR weight_grams >= 0", name="packaging_weight_non_negative"
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="packaging_finished_after_started",
        ),
    )

    #: The number a packer says out loud, e.g. `PK-002147`.
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    #: A real foreign key across the context boundary, exactly as
    #: `postproduction_tasks` and `print_jobs` do: there is one database and one
    #: domain model, and an order that no longer exists has nothing to pack.
    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[PackStatus] = mapped_column(
        enum_column(PackStatus), nullable=False, default=PackStatus.CHECKED
    )

    #: How it leaves — a `DeliveryMethod` value, denormalised at creation so the
    #: board can group by the van without joining `orders` on every refresh.
    delivery_method: Mapped[str] = mapped_column(String(16), nullable=False, default="pickup")
    #: Which carrier's van, when there is one. A code the client renders.
    carrier_code: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    #: When that van comes. The post's actual deadline — see `policies`.
    cutoff_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: Total pieces the parcel must contain, summed from the order's lines. The
    #: figure the completeness check counts against.
    items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: What the parts weigh, from the estimates the order was priced on.
    estimated_grams: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    #: The batch's bounding box, from `policies.batch_box`. The basis of the
    #: recommended tara and of the volumetric weight shown beside the real one.
    length_mm: Mapped[Decimal] = mapped_column(Numeric(8, 1), nullable=False, default=Decimal(0))
    width_mm: Mapped[Decimal] = mapped_column(Numeric(8, 1), nullable=False, default=Decimal(0))
    height_mm: Mapped[Decimal] = mapped_column(Numeric(8, 1), nullable=False, default=Decimal(0))
    #: Whether film is mandatory for this parcel, decided by the thinnest wall.
    wrap_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: What the packer actually put it in. ``SET NULL``: retiring a box from the
    #: catalogue must not erase what last month's parcels were shipped in.
    tara_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("packaging_tara.id", ondelete="SET NULL"), nullable=True
    )
    #: What the scales said. Null until the weighing step — the fact half of the
    #: pair whose other half is `estimated_grams`.
    weight_grams: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    #: Summed from the consumption ledger when the parcel is closed.
    packaging_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal(0)
    )

    norm_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    instruction_version: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    operator_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Time actually spent, banked across interruptions. A parcel left while the
    #: packer answered the door did not take forty minutes.
    elapsed_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    running_since: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: Why it cannot be closed, when it cannot. A code, never prose (ADR-0012).
    hold_reason: Mapped[HoldReason | None] = mapped_column(
        enum_column(HoldReason), nullable=True, default=None
    )
    #: What the packer found missing or wrong at the completeness check.
    discrepancy_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    discrepancy_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: When it was found. Its own column rather than `updated_at`, which moves
    #: whenever anything on the row does: a parcel short-counted in June and
    #: shipped in August would otherwise count as an August discrepancy, and
    #: «62 дня без недовложений» would reset on a touch nobody could connect
    #: to it. Both figures are ones the post is judged on.
    discrepancy_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    steps: Mapped[list[PackStep]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="PackStep.position",
    )
    used: Mapped[list[PackUse]] = relationship(back_populates="task", cascade="all, delete-orphan")


class PackStep(Entity):
    """One step of one parcel, and what it actually cost.

    A copy of the instruction step rather than a pointer at it: this is the record
    of what somebody was told to do, and republishing version 2.2 mid-shift must
    not rewrite the parcel a packer is halfway through.
    """

    __tablename__ = "packaging_task_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "position", name="uq_packaging_task_step_position"),
        CheckConstraint("position >= 1", name="packaging_task_step_position_positive"),
        CheckConstraint("norm_minutes >= 0", name="packaging_task_step_norm_non_negative"),
        CheckConstraint(
            "actual_minutes IS NULL OR actual_minutes >= 0",
            name="packaging_task_step_actual_non_negative",
        ),
    )

    task_id: Mapped[EntityId] = mapped_column(
        ForeignKey("packaging_tasks.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    warning: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    norm_minutes: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal(1))
    actual_minutes: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    task: Mapped[PackTask] = relationship(back_populates="steps")


class PackUse(Entity):
    """One tara item consumed by one parcel.

    A ledger rather than a counter on `Tara`, because "расход за месяц" and "хватит
    на" are the two figures the purchasing decision is made from, and a running
    total that was only ever decremented cannot answer either. It also makes the
    cost of a parcel reconstructable years later, which a decremented stock level
    is not.
    """

    __tablename__ = "packaging_task_tara"
    __table_args__ = (
        UniqueConstraint("task_id", "tara_id", name="uq_packaging_use"),
        Index("ix_packaging_task_tara_tara_id", "tara_id"),
        CheckConstraint("quantity > 0", name="packaging_use_quantity_positive"),
    )

    task_id: Mapped[EntityId] = mapped_column(
        ForeignKey("packaging_tasks.id", ondelete="CASCADE"), nullable=False
    )
    #: ``RESTRICT``: the consumption history is what the tara table is computed
    #: from, and deleting a box out from under it would silently rewrite last
    #: month's spend.
    tara_id: Mapped[EntityId] = mapped_column(
        ForeignKey("packaging_tara.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(1))
    #: What it cost at the moment it was used. Pinned, for the same reason an
    #: order pins its rate snapshot: a price rise must not restate July's parcels.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal(0))

    task: Mapped[PackTask] = relationship(back_populates="used")


__all__ = [
    "PackInstruction",
    "PackInstructionStep",
    "PackStep",
    "PackTask",
    "PackUse",
    "Tara",
]
