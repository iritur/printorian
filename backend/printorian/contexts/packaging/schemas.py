"""DTOs crossing the packing boundary.

Every name here is prefixed `Pack` even where a bare noun would read better —
`PackColumn`, not `Column`. Two contexts on one OpenAPI document cannot both own
a component called `Column`, and a generated client that silently renamed one of
them would be a debugging afternoon nobody has budgeted for.

The comparison rule from `postproduction` holds: a figure arrives beside what it
is judged against. A step carries its norm and its fact, a parcel carries its
estimated weight and the one the scales gave, a box carries what it costs and
what the estimate allowed for.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.packaging.policies import HoldReason, PackStatus, TaraKind
from printorian.contexts.postproduction import Badge, Urgency
from printorian.core.ids import EntityId


class PackStepView(BaseModel):
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


class PackLine(BaseModel):
    """One row of the completeness check.

    Resolved by the delivery layer from the order, not stored: what the customer
    bought is `ordering`'s fact, and copying it here would give the farm two
    answers to "how many did they order" that could disagree.
    """

    model_name: str
    color: str = ""
    ordered: int
    #: What the packer counted. Equal to `ordered` until somebody says otherwise.
    present: int


class PackView(BaseModel):
    """One card on the board, and everything the popup behind it shows."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    number: str
    status: PackStatus
    order_id: EntityId
    #: Filled by the delivery layer — this context knows the order's id, not what
    #: it is called. Unresolved is empty, never a raw id.
    order_number: str = ""
    delivery_method: str
    carrier_code: str = ""

    cutoff_at: datetime | None = None
    #: Which band the card is drawn in, from the cutoff and nothing else.
    urgency: Urgency = Urgency.OK
    #: Signed: negative means the van has already gone.
    minutes_to_cutoff: Decimal | None = None

    items: int
    estimated_grams: Decimal
    length_mm: Decimal
    width_mm: Decimal
    height_mm: Decimal
    #: The carrier's figure when the parcel is bigger than it is heavy.
    volumetric_grams: Decimal = Decimal(0)
    wrap_required: bool = False

    tara_id: EntityId | None = None
    #: What is written on the shelf, not the supplier's code. The screen only
    #: ever renders this, and a card naming `box-a` made the packer translate.
    tara_name: str = ""
    #: What the geometry says should be used, whether or not the packer agreed.
    recommended_tara_id: EntityId | None = None
    recommended_tara_name: str = ""
    weight_grams: Decimal | None = None
    packaging_cost: Decimal = Decimal(0)

    norm_minutes: Decimal
    elapsed_minutes: Decimal
    instruction_version: str = ""
    #: Norm over fact once there is a fact. Above 100 is faster than the norm.
    pace_percent: Decimal | None = None
    #: Elapsed plus the remaining steps' norms — what this parcel looks like it
    #: will cost, offered while there is still time to act on it.
    projected_minutes: Decimal | None = None

    operator_id: EntityId | None = None
    operator_name: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    shipped_at: datetime | None = None

    hold_reason: HoldReason | None = None
    discrepancy_code: str | None = None
    discrepancy_note: str | None = None
    discrepancy_at: datetime | None = None

    steps: list[PackStepView] = Field(default_factory=list)
    lines: list[PackLine] = Field(default_factory=list)

    @property
    def step_position(self) -> int:
        """Which step is next, 1-based. Equals ``len(steps) + 1`` when finished."""
        return sum(1 for step in self.steps if step.done_at is not None) + 1


class PackColumn(BaseModel):
    """One column of the board."""

    status: PackStatus
    tasks: list[PackView] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tasks)


class TaraRow(BaseModel):
    """One row of the tara table: what it costs, what it goes at, how long it lasts."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    code: str
    name: str
    kind: TaraKind
    unit: str
    inner_length_mm: Decimal | None = None
    inner_width_mm: Decimal | None = None
    inner_height_mm: Decimal | None = None
    price: Decimal
    stock: Decimal
    reorder_at: Decimal
    #: Consumed over the last `STATS_DAYS`, summed from the ledger.
    used_per_month: Decimal = Decimal(0)
    #: Stock divided by that rate. ``None`` when nothing has been consumed — a box
    #: nobody uses does not last for ever, it simply has no measured rate.
    months_left: Decimal | None = None

    @property
    def is_low(self) -> bool:
        return self.reorder_at > 0 and self.stock <= self.reorder_at


class PackKpi(BaseModel):
    """The four tiles above the board."""

    queued: int
    #: Queued, split by how each one leaves. The line under the first tile.
    queued_by_method: list[tuple[str, int]] = Field(default_factory=list)
    #: Queued and inside the warning band, or already past the van.
    urgent: int
    #: How many of the queued must go on today's last pickup.
    due_before_cutoff: int
    packed_today: int
    packed_yesterday: int
    #: Minutes per parcel over the window, against the instruction's own total.
    average_minutes: Decimal | None = None
    norm_minutes: Decimal | None = None
    pace_percent: Decimal | None = None
    #: Days since the last recorded discrepancy. ``None`` when there has never
    #: been one *and* nothing has shipped — no record is not a clean record.
    days_without_discrepancy: int | None = None
    discrepancies: int
    #: Mean packing cost per parcel over the window, and what the farm budgets.
    cost_per_parcel: Decimal | None = None


class PackMetrics(BaseModel):
    """The thirty-day panel. Facts only; nothing here is a target."""

    days: int
    packed: int
    average_minutes: Decimal | None = None
    #: How often the recommended box was the box actually used. The honest gauge
    #: on `policies.stack_box`, which is deliberately crude and says so.
    tara_accuracy_percent: Decimal | None = None
    discrepancies: int
    #: ``None`` until logistics can mark a shipment damaged. A zero here would
    #: claim a clean record that has not been measured.
    damages: int | None = None
    missed_cutoffs: int
    cost_per_parcel: Decimal | None = None
    #: Pace, quality and volume on a ten-point scale — the same formula the
    #: scorecards use, applied to the whole post.
    score: Decimal | None = None


class PackScore(BaseModel):
    """One packer's shift. Every figure is a recorded fact."""

    operator_id: EntityId
    operator_name: str
    packed: int
    average_minutes: Decimal | None = None
    discrepancies: int
    pace_percent: Decimal | None = None
    score: Decimal | None = None
    badges: list[Badge] = Field(default_factory=list)


class PickupView(BaseModel):
    """One van, and what is going on it."""

    #: A `DeliveryMethod` value, or a carrier code when the farm has several.
    method: str
    carrier_code: str = ""
    at: datetime | None = None
    parcels: int


class PackBoard(BaseModel):
    """The whole screen, read at one instant."""

    at: datetime
    #: The next cutoff of the day, which the header counts down to.
    next_cutoff_at: datetime | None = None
    columns: list[PackColumn] = Field(default_factory=list)
    kpi: PackKpi
    tara: list[TaraRow] = Field(default_factory=list)
    metrics: PackMetrics
    shift: list[PackScore] = Field(default_factory=list)
    pickups: list[PickupView] = Field(default_factory=list)


# --------------------------------------------------------------- write models


class CreatePackTask(BaseModel):
    """Raising a parcel. The sweep's job, not a person's."""

    order_id: EntityId
    delivery_method: str = Field(default="pickup", max_length=16)
    carrier_code: str = Field(default="", max_length=40)
    cutoff_at: datetime | None = None
    items: int = Field(default=0, ge=0)
    estimated_grams: Decimal = Field(default=Decimal(0), ge=0)
    length_mm: Decimal = Field(default=Decimal(0), ge=0)
    width_mm: Decimal = Field(default=Decimal(0), ge=0)
    height_mm: Decimal = Field(default=Decimal(0), ge=0)
    wrap_required: bool = False


class TickStep(BaseModel):
    """Ticking one step off."""

    position: int = Field(ge=1)


class ChooseTara(BaseModel):
    """What the packer actually reached for, and what else went in the box."""

    tara_id: EntityId
    #: Film, filler, anything consumed beyond the enclosure itself.
    extras: dict[EntityId, Decimal] = Field(default_factory=dict)


class Weigh(BaseModel):
    """What the scales said, at the weighing step."""

    weight_grams: Decimal = Field(ge=0)


class HoldParcel(BaseModel):
    """Parking a parcel on somebody else's problem."""

    reason: HoldReason
    note: str | None = Field(default=None, max_length=1000)


class ReportDiscrepancy(BaseModel):
    """The completeness check found something. Stops the parcel where it stands."""

    #: A code the client renders, e.g. `discrepancy.short_count`.
    discrepancy_code: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class CreateTara(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    kind: TaraKind
    unit: str = Field(default="piece", max_length=20)
    inner_length_mm: Decimal | None = Field(default=None, gt=0)
    inner_width_mm: Decimal | None = Field(default=None, gt=0)
    inner_height_mm: Decimal | None = Field(default=None, gt=0)
    price: Decimal = Field(default=Decimal(0), ge=0)
    stock: Decimal = Field(default=Decimal(0), ge=0)
    reorder_at: Decimal = Field(default=Decimal(0), ge=0)


class CreatePackStep(BaseModel):
    position: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    detail: str | None = Field(default=None, max_length=1000)
    warning: str | None = Field(default=None, max_length=1000)
    norm_minutes: Decimal = Field(default=Decimal(1), ge=0)


class PublishInstruction(BaseModel):
    """A new version, with its steps and the reason it changed."""

    version: str = Field(min_length=1, max_length=16)
    reason: str | None = Field(default=None, max_length=300)
    steps: list[CreatePackStep] = Field(default_factory=list)


__all__ = [
    "ChooseTara",
    "CreatePackStep",
    "CreatePackTask",
    "CreateTara",
    "HoldParcel",
    "PackBoard",
    "PackColumn",
    "PackKpi",
    "PackLine",
    "PackMetrics",
    "PackScore",
    "PackStepView",
    "PackView",
    "PickupView",
    "PublishInstruction",
    "ReportDiscrepancy",
    "TaraRow",
    "TickStep",
    "Weigh",
]
