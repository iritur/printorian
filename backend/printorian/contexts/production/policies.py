"""The life of one print job.

Separate from `OrderStatus`: an order is a commercial promise, a job is a plate on
a machine. One order can need several attempts — a failed print is remade without
the customer's order going backwards — so the two cannot share a state machine.
"""

from __future__ import annotations

from enum import StrEnum

from printorian.core.errors import DomainRuleViolationError


class JobStatus(StrEnum):
    """Where a job is between "ordered" and "off the bed"."""

    #: Created, waiting for an engineer to prepare a plate (ADR-0006).
    PENDING = "pending"
    #: Sliced, but the truth cost more than the quote by more than the farm will
    #: absorb (ADR-0013). Deliberately not `PENDING`: nothing is waiting to be
    #: sliced, somebody has to decide about money.
    ON_HOLD = "on_hold"
    #: A plate exists. From here the scheduler may pick it up.
    READY = "ready"
    #: The planner chose a machine; nothing has been sent yet.
    ASSIGNED = "assigned"
    #: Uploading and starting. Deliberately its own state — an upload that dies
    #: half way must not look like either "queued" or "printing".
    DISPATCHING = "dispatching"
    #: The machine has confirmed it is running this job.
    PRINTING = "printing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}

    @property
    def occupies_printer(self) -> bool:
        """Whether this job is holding a machine it must be released from."""
        return self in {JobStatus.ASSIGNED, JobStatus.DISPATCHING, JobStatus.PRINTING}


#: The only transitions the system performs. Anything else is a bug rather than a
#: business decision, and is refused loudly instead of half-applied.
TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.READY, JobStatus.ON_HOLD, JobStatus.CANCELLED}),
    # Released once the price is settled, or dropped. It never goes back to
    # PENDING — the plate exists, and re-slicing it would not be the fix.
    JobStatus.ON_HOLD: frozenset({JobStatus.READY, JobStatus.CANCELLED}),
    JobStatus.READY: frozenset({JobStatus.ASSIGNED, JobStatus.CANCELLED}),
    # Back to READY covers re-planning: an assignment is a plan, not a commitment,
    # until something has actually been sent to a machine.
    JobStatus.ASSIGNED: frozenset({JobStatus.DISPATCHING, JobStatus.READY, JobStatus.CANCELLED}),
    # A failed dispatch returns to READY so another machine can be tried. It is not
    # a failed *print* — nothing was printed, and no material was spent.
    JobStatus.DISPATCHING: frozenset(
        {JobStatus.PRINTING, JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.PRINTING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}),
    # A remake re-enters the queue rather than starting a new job, so the attempts
    # stay attached to the thing the customer ordered.
    JobStatus.FAILED: frozenset({JobStatus.READY, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise DomainRuleViolationError(
            "error.production.invalid_transition",
            current=current.value,
            target=target.value,
            allowed=sorted(state.value for state in TRANSITIONS[current]),
        )


#: Codes the dispatcher emits when a job cannot be sent. Prose lives in the clients
#: (ADR-0012). Named here rather than in `service` because they are part of the
#: production vocabulary — the console renders them and the tests assert on them —
#: and `policies` is where this context keeps rules worth naming.
DISPATCH_UPLOAD_FAILED = "error.production.upload_failed"
DISPATCH_START_FAILED = "error.production.start_failed"
DISPATCH_NO_PLATE = "error.production.no_plate"
DISPATCH_NO_PRINTER = "error.production.no_printer"
