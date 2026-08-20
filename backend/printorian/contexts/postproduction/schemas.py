"""DTOs crossing the post-production boundary.

Every figure that can be compared arrives beside the thing it is compared
against: a step carries its norm *and* its fact, a task carries its elapsed time
*and* its norm, an operator carries their pace *and* the shop's. A screen that
had to fetch the second half separately would eventually show one without the
other, and half of a comparison is worse than none.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.postproduction.policies import OperationKind, TaskStatus, Urgency
from printorian.core.ids import EntityId


class StepView(BaseModel):
    """One numbered step, with its norm and — once ticked — its fact."""

    model_config = ConfigDict(from_attributes=True)

    position: int
    title: str
    detail: str | None = None
    warning: str | None = None
    norm_minutes: Decimal
    actual_minutes: Decimal | None = None
    done_at: datetime | None = None

    @property
    def is_done(self) -> bool:
        return self.done_at is not None


class TaskView(BaseModel):
    """One card on the board, and everything the popup behind it shows."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    number: str
    status: TaskStatus
    kind: OperationKind
    order_id: EntityId
    #: Filled by the delivery layer — this context knows the order's id and not
    #: what it is called. An unresolved value is empty, never a raw id.
    order_number: str = ""
    model_name: str
    material_code: str
    colors: list[str] = Field(default_factory=list)
    printer_id: EntityId | None = None
    quantity: int

    due_at: datetime | None = None
    #: Which band the card is drawn in, derived from `due_at` and nothing else.
    urgency: Urgency = Urgency.OK
    #: Signed: negative means the promise has already passed.
    minutes_to_due: Decimal | None = None

    norm_minutes: Decimal
    elapsed_minutes: Decimal
    instruction_version: str = ""
    #: Norm over fact once there is a fact. Above 100 is faster than the norm.
    pace_percent: Decimal | None = None
    #: Elapsed plus the remaining steps' norms — what this batch looks like it
    #: will cost, offered while there is still time to act on it.
    projected_minutes: Decimal | None = None

    operator_id: EntityId | None = None
    operator_name: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cure_until: datetime | None = None

    attempt: int = 1
    defect_code: str | None = None
    defect_note: str | None = None

    steps: list[StepView] = Field(default_factory=list)

    @property
    def step_position(self) -> int:
        """Which step is next, 1-based. Equals ``len(steps) + 1`` when finished."""
        return sum(1 for step in self.steps if step.done_at is not None) + 1


class Column(BaseModel):
    """One column of the board."""

    status: TaskStatus
    tasks: list[TaskView] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tasks)


class OperationStat(BaseModel):
    """One row of "operations over 30 days": fact against norm."""

    kind: OperationKind
    completed: int
    norm_minutes: Decimal
    actual_minutes: Decimal
    pace_percent: Decimal | None = None
    returns: int


class ShiftKpi(BaseModel):
    """The four tiles above the board."""

    queued: int
    queued_by_kind: list[tuple[OperationKind, int]] = Field(default_factory=list)
    urgent: int
    completed_today: int
    completed_yesterday: int
    #: Returns as a share of completions over the window. ``None`` when nothing
    #: completed — a shift that finished nothing has no quality figure.
    quality_percent: Decimal | None = None
    returns: int
    pace_percent: Decimal | None = None
    shop_pace_percent: Decimal | None = None


class Badge(BaseModel):
    """One earned — or visibly unearned — mark.

    ``tier`` is 0 for not yet earned, then 1–3. Monochrome by design: colour is
    reserved for machine state, and a badge is not a state. Unearned badges are
    returned rather than omitted, so there is something visible to earn.
    """

    code: str
    tier: int
    #: The figures behind it, rendered by the client into its own sentence.
    detail: dict[str, str] = Field(default_factory=dict)


class Scorecard(BaseModel):
    """One operator's accumulated evaluation. Every figure is a recorded fact."""

    operator_id: EntityId
    operator_name: str
    completed: int
    returns: int
    pace_percent: Decimal | None = None
    #: Pace × quality × volume, on a ten-point scale. The formula is stated on the
    #: screen, because a score nobody can reconstruct is a score nobody accepts.
    score: Decimal | None = None
    is_trainee: bool = False
    badges: list[Badge] = Field(default_factory=list)


class ConsumableView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    code: str
    name: str
    unit: str
    remaining: Decimal
    reorder_at: Decimal

    @property
    def is_low(self) -> bool:
        return self.reorder_at > 0 and self.remaining <= self.reorder_at


class Board(BaseModel):
    """The whole screen, read at one instant."""

    at: datetime
    columns: list[Column] = Field(default_factory=list)
    kpi: ShiftKpi
    operations: list[OperationStat] = Field(default_factory=list)
    shift: list[Scorecard] = Field(default_factory=list)
    consumables: list[ConsumableView] = Field(default_factory=list)
    #: Completed tasks per day, for the shift sparkline.
    output_by_day: list[tuple[datetime, int]] = Field(default_factory=list)


# --------------------------------------------------------------- write models


class CreateTask(BaseModel):
    """Raising a task. Normally the print-finished handler's job, not a person's."""

    order_id: EntityId
    kind: OperationKind
    model_name: str = Field(default="", max_length=300)
    material_code: str = Field(default="", max_length=80)
    colors: list[str] = Field(default_factory=list)
    printer_id: EntityId | None = None
    quantity: int = Field(default=1, ge=1)
    due_at: datetime | None = None


class CompleteStep(BaseModel):
    """Ticking one step off."""

    position: int = Field(ge=1)


class ReportDefect(BaseModel):
    """Sending a batch back from quality control."""

    #: A code the client renders, e.g. `defect.paint_run`.
    defect_code: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class CreateOperation(BaseModel):
    kind: OperationKind
    norm_minutes_per_unit: Decimal = Field(default=Decimal(5), gt=0)
    cure_minutes: int = Field(default=0, ge=0)
    instruction_version: str = Field(default="1.0", max_length=16)


class CreateStep(BaseModel):
    position: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    detail: str | None = Field(default=None, max_length=1000)
    warning: str | None = Field(default=None, max_length=1000)
    norm_minutes: Decimal = Field(default=Decimal(1), ge=0)


__all__ = [
    "Badge",
    "Board",
    "Column",
    "CompleteStep",
    "ConsumableView",
    "CreateOperation",
    "CreateStep",
    "CreateTask",
    "OperationStat",
    "ReportDefect",
    "Scorecard",
    "ShiftKpi",
    "StepView",
    "TaskView",
]
