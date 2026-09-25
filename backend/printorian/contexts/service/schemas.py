"""DTOs crossing the service boundary.

Note what is absent, because it is the same absence `fleet/schemas.py` opens with
and for a stricter reason: **no field here carries money or energy.** The kit's
«Последствия» panel prices a failure — «ПОТЕРЯ 3 820 ₽», «Итого потеря 2 140 ₽» —
and every input to that multiplication (`Printer.amortization_per_hour`,
`nominal_power_kw`) is one import away. A response a `VIEW_PRODUCTION` role can
read must never start carrying rubles; the composition that would is a different
permission and a different route.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.service.policies import (
    FailureCause,
    FailureOrigin,
    TicketKind,
    TicketStatus,
)
from printorian.core.ids import EntityId


class FailureView(BaseModel):
    """One failure as it is read back."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    printer_id: EntityId
    origin: FailureOrigin
    #: ``None`` until somebody names one. Never guessed from `error_code`.
    cause: FailureCause | None = None
    error_code: str | None = None
    detected_at: datetime
    #: ``None`` while the machine is still down. A client must render this as
    #: «идёт» rather than as a repair that took no time.
    restored_at: datetime | None = None
    note: str | None = None
    recorded_by: EntityId | None = None


class RecordFailure(BaseModel):
    """A person recording a failure against a machine.

    `detected_at` is optional and defaults to the clock, because a person
    recording a failure they are standing in front of has nothing better to
    offer. The sweep does — the machine's own `last_seen_at` — and passes it.
    """

    printer_id: EntityId
    cause: FailureCause | None = None
    error_code: str | None = Field(default=None, max_length=120)
    detected_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)


class RestoreFailure(BaseModel):
    """The observation in which the machine was working again."""

    restored_at: datetime


class NameCause(BaseModel):
    """Naming the cause of a failure the driver opened with none."""

    cause: FailureCause


# ------------------------------------------------------------------ tickets


class TicketStepView(BaseModel):
    """One line of «Порядок работ» as it is read back."""

    model_config = ConfigDict(from_attributes=True)

    position: int
    title: str
    note: str | None = None
    #: ``None`` is "no norm", never zero minutes.
    norm_minutes: int | None = None
    done_at: datetime | None = None
    done_by: EntityId | None = None


class TicketView(BaseModel):
    """One ticket as the board and the detail read it. Minutes and counts; no money."""

    id: EntityId
    number: str
    kind: TicketKind
    status: TicketStatus
    #: Who raised it. `DRIVER` with a `failure_id` is the kit's «СООБЩИЛ ДРАЙВЕР».
    origin: FailureOrigin
    printer_id: EntityId | None = None
    failure_id: EntityId | None = None
    #: Empty for a driver-opened ticket — the client draws that from `origin`.
    title: str = ""
    note: str | None = None
    norm_minutes: int | None = None
    opened_at: datetime
    started_at: datetime | None = None
    closed_at: datetime | None = None
    opened_by: EntityId | None = None
    assignee_id: EntityId | None = None
    #: Since `started_at` (or `opened_at` while merely raised) until `closed_at`
    #: or the read — the kit's «41 М» / «28 М ИЗ 2 Ч». Measured against the
    #: clock the caller holds, so two reads of one board agree with each other.
    elapsed_seconds: int = 0
    steps: list[TicketStepView] = Field(default_factory=list)
    steps_done: int = 0


class TicketBoard(BaseModel):
    """The kit's five lanes. A ticket is in exactly one — `tickets.lanes_of` decides."""

    emergency: list[TicketView] = Field(default_factory=list)
    planned: list[TicketView] = Field(default_factory=list)
    in_progress: list[TicketView] = Field(default_factory=list)
    logistics: list[TicketView] = Field(default_factory=list)
    #: Closed at or after `closed_since` — «Закрыто сегодня», bounded by time so a
    #: busy day does not hide its own morning.
    closed: list[TicketView] = Field(default_factory=list)
    closed_since: datetime | None = None


class AddStep(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    norm_minutes: int | None = Field(default=None, gt=0)


class RaiseTicket(BaseModel):
    """A person raising work: «Создать заявку»."""

    kind: TicketKind
    title: str = Field(min_length=1, max_length=200)
    printer_id: EntityId | None = None
    note: str | None = Field(default=None, max_length=1000)
    norm_minutes: int | None = Field(default=None, gt=0)
    assignee_id: EntityId | None = None
    steps: list[AddStep] = Field(default_factory=list)


class AssignTicket(BaseModel):
    #: ``None`` takes the ticket off whoever held it.
    assignee_id: EntityId | None = None


__all__ = [
    "AddStep",
    "AssignTicket",
    "FailureView",
    "NameCause",
    "RaiseTicket",
    "RecordFailure",
    "RestoreFailure",
    "TicketBoard",
    "TicketStepView",
    "TicketView",
]
