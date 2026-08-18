"""Events published by the production context.

These are what the floor display and the customer cabinet react to. The scheduler
also listens: a machine becoming free is the moment to re-plan the wait list
(ARCHITECTURE §6), rather than waiting for the next tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from printorian.core.events import Event
from printorian.core.ids import EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class JobReady(Event):
    """A plate exists and the job can be scheduled."""

    name: ClassVar[str] = "job.ready"

    job_id: EntityId
    order_id: EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class JobAssigned(Event):
    name: ClassVar[str] = "job.assigned"

    job_id: EntityId
    order_id: EntityId
    printer_id: EntityId
    printer_name: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class JobStarted(Event):
    """The machine confirmed it is running the plate."""

    name: ClassVar[str] = "job.started"

    job_id: EntityId
    order_id: EntityId
    printer_id: EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class JobSucceeded(Event):
    name: ClassVar[str] = "job.succeeded"

    job_id: EntityId
    order_id: EntityId
    printer_id: EntityId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class JobFailed(Event):
    """A print that was started and did not finish.

    Distinct from a dispatch that never got off the ground: this one spent
    material and machine hours, and the plate on the bed has to be cleared.
    """

    name: ClassVar[str] = "job.failed"

    job_id: EntityId
    order_id: EntityId
    printer_id: EntityId | None = None
    #: An error code, never prose (ADR-0012).
    failure_code: str = ""
    attempt: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class JobWaitListed(Event):
    """Nothing can take this job yet.

    Carries the reason so the cabinet can tell a customer whether they are waiting
    for a machine to free up or for a person to do something.
    """

    name: ClassVar[str] = "job.wait_listed"

    job_id: EntityId
    order_id: EntityId
    reason: str
    #: ISO-8601, or empty when no honest prediction exists.
    predicted_start: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PrinterBecameFree(Event):
    """A machine finished and can be re-planned onto.

    The scheduler's own trigger: waiting for the next tick would leave a printer
    idle for up to the tick interval with work sitting in the queue.
    """

    name: ClassVar[str] = "printer.became_free"

    printer_id: EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class PlateVarianceExceeded(Event):
    """Slicing found the job costs more than the farm will absorb (ADR-0013).

    Production does not move the order itself — `PriceReview` is a state in the
    *order* machine, and reaching across to set it would put one context's rules
    inside another. This says what was found; ordering decides.
    """

    name: ClassVar[str] = "plate.variance_exceeded"

    job_id: EntityId
    order_id: EntityId
    #: Decimal strings: money never crosses a wire as a float.
    quoted_cost: str = "0"
    prepared_cost: str = "0"
    overrun_ratio: str = "0"


@dataclass(frozen=True, slots=True, kw_only=True)
class PlatePrepared(Event):
    """An engineer sliced a configuration; every later order of it can skip prep."""

    name: ClassVar[str] = "plate.prepared"

    plate_id: EntityId
    plate_key: str = ""
