"""Views over jobs, assignments and the wait list."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.production.policies import JobStatus
from printorian.core.ids import EntityId


class JobEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    from_status: str | None = None
    to_status: str
    reason: str = ""
    created_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class JobView(BaseModel):
    """One job, as the floor and the cabinet show it."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    order_id: EntityId
    status: JobStatus
    printer_id: EntityId | None = None
    attempt: int = 1

    model_asset_id: EntityId | None = None
    model_hash: str = ""
    scale: Decimal = Decimal(1)
    material_type: str = ""
    colors: list[str] = Field(default_factory=list)
    grams_required: Decimal = Decimal(0)
    estimated_minutes: Decimal = Decimal(0)

    plate_filename: str | None = None
    due_at: datetime | None = None
    priority: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress_percent: int | None = None
    failure_code: str | None = None

    events: list[JobEventView] = Field(default_factory=list)


class CandidateView(BaseModel):
    """One machine's fate in a planning pass."""

    printer_id: str
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    components: list[dict[str, Any]] = Field(default_factory=list)
    score: Decimal | None = None


class AssignmentRecordView(BaseModel):
    """The persisted audit record: every candidate, and how each fared."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    job_id: EntityId
    chosen_printer_id: EntityId | None = None
    winning_score: Decimal | None = None
    candidates: list[CandidateView] = Field(default_factory=list)
    created_at: datetime


class WaitListEntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    job_id: EntityId
    order_id: EntityId
    reason: str
    #: Null whenever the wait needs a person rather than time.
    predicted_start: datetime | None = None
    blocking_reasons: list[str] = Field(default_factory=list)


class EstimateVarianceView(BaseModel):
    """What slicing found, against what the customer was quoted.

    Every recorded variance is here, not only the ones that exceeded the band.
    ADR-0013 keeps the in-band ones deliberately: they are the farm absorbing
    small differences, and they are the dataset ROADMAP Phase 6 calibrates the
    mesh estimator against. A view that served only the escalations would leave
    the estimator learning from its worst cases alone.

    Nothing is derived here — no delta, no percentage. The four measured pairs are
    carried as they were recorded and the client subtracts. A computed field would
    be a second place the variance arithmetic lives, and `prep.assess_variance` is
    the first (ADR-0013).
    """

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    job_id: EntityId
    order_id: EntityId

    #: Money, which is why the route carries `VIEW_FINANCIALS` on top of the
    #: router's production permission. CLAUDE.md §1: a response about seconds must
    #: not quietly start carrying rubles.
    quoted_cost: Decimal
    prepared_cost: Decimal
    #: The band in force when this was judged, stored per variance rather than
    #: read from configuration now — the same reason a rate snapshot is pinned.
    tolerance: Decimal
    within_tolerance: bool

    #: The manufacturing pair behind the money. What the estimator is actually
    #: calibrated on: a price is the product of an estimate and a tariff, and only
    #: one of those is the estimator's to get right.
    estimated_minutes: Decimal
    prepared_minutes: Decimal
    estimated_grams: Decimal
    prepared_grams: Decimal

    created_at: datetime


class CreateJob(BaseModel):
    """What the ordering side hands to production once an order is paid."""

    order_id: EntityId
    #: The geometry the line was priced from, carried through from the order.
    model_asset_id: EntityId | None = None
    model_hash: str = Field(default="", max_length=64)
    scale: Decimal = Decimal(1)
    material_type: str = Field(min_length=1, max_length=80)
    colors: list[str] = Field(default_factory=list)
    width_mm: Decimal = Decimal(0)
    depth_mm: Decimal = Decimal(0)
    height_mm: Decimal = Decimal(0)
    nozzle_diameter_mm: Decimal | None = None
    grams_required: Decimal = Decimal(0)
    estimated_minutes: Decimal = Decimal(0)
    due_at: datetime | None = None
    priority: int = 0


class PlanOutcome(BaseModel):
    """What one planning pass did."""

    assigned: int = 0
    wait_listed: int = 0
    considered: int = 0


class QueuePosition(BaseModel):
    """Where a customer's work stands, in terms they can act on.

    Deliberately small. A customer needs to know whether their thing is moving,
    not which machine was rejected on what grounds — that is the floor's business
    (`AssignmentRecordView`).
    """

    job_status: JobStatus
    #: 1-based place among jobs waiting for capacity. Null when the job is not
    #: queueing — it is printing, or held, or waiting on a person.
    position: int | None = None
    #: Why it is waiting, as a code the client renders (ADR-0012).
    reason: str | None = None
    #: Only when time alone will resolve the wait. Null means a person is needed,
    #: and the client says so rather than showing a date nobody can stand behind.
    predicted_start: datetime | None = None
    progress_percent: int | None = None

    #: Which attempt this is. Above one means an earlier print failed and the
    #: farm is reprinting at its own cost — the customer should be told that
    #: rather than left wondering why the promised date moved.
    attempt: int = 1
    #: The machine chosen, when one has been. Resolved to a name by the API,
    #: which is the only layer allowed to know both contexts.
    printer_id: EntityId | None = None
    #: When the planner picked a machine, and when the machine confirmed it was
    #: running. Both from the job's own event log, so the pipeline can date its
    #: stages from what happened rather than from when somebody looked.
    assigned_at: datetime | None = None
    started_at: datetime | None = None
