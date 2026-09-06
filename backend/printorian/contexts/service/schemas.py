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

from printorian.contexts.service.policies import FailureCause, FailureOrigin
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


__all__ = ["FailureView", "NameCause", "RecordFailure", "RestoreFailure"]
