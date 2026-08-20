"""The life of one post-production task, and the norms it is measured against.

Three named rules live here because they are the ones people will argue about:
which state a task may move to, what counts as "on norm", and what a trainee's
norm is.

**The norm is a gauge, not a stick.** It is shown next to the step *before* the
step is started, which is the whole reason it is modelled per step rather than
only per task: a norm you only hear about when you miss it teaches nothing, and
the same norm shown beside the checkbox tells a new operator how long the job
should take.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from printorian.core.errors import DomainRuleViolationError


class OperationKind(StrEnum):
    """What is being done to the part.

    A closed set rather than free text: every one of these carries its own time
    norm, its own instruction and its own return rate, and an operation nobody
    named cannot be measured. New kinds are a code change, which is correct —
    adding one means writing its instruction.
    """

    SUPPORT_REMOVAL = "support_removal"
    SANDING = "sanding"
    PRIMING = "priming"
    PAINTING = "painting"
    POLISHING = "polishing"
    ASSEMBLY = "assembly"


#: Operations whose parts have to stand and dry before anything else touches them.
#: The board gives these their own column, because a part that is *waiting on
#: chemistry* is not idle work and must not be picked up by the next free operator.
CURES: frozenset[OperationKind] = frozenset({OperationKind.PRIMING, OperationKind.PAINTING})


class TaskStatus(StrEnum):
    """Where a task is between "off the bed" and "packed"."""

    #: Created when the print finished. Nobody has picked it up.
    WAITING = "waiting"
    #: An operator is on it, and the clock is running.
    IN_PROGRESS = "in_progress"
    #: Paused by the operator. Deliberately distinct from `WAITING`: the task is
    #: still theirs, and the elapsed time already spent is still theirs too.
    PAUSED = "paused"
    #: Standing under a fan. Nothing to do until the timer runs out.
    CURING = "curing"
    #: Work finished, awaiting quality control.
    FOR_QC = "for_qc"
    #: QC rejected it. Not `WAITING` — the reason matters and the operator who did
    #: it should see it come back, so the board keeps this as its own column.
    RETURNED = "returned"
    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {TaskStatus.DONE, TaskStatus.CANCELLED}

    @property
    def holds_an_operator(self) -> bool:
        """Whether somebody is currently accountable for this task."""
        return self in {TaskStatus.IN_PROGRESS, TaskStatus.PAUSED}


#: The only transitions the system performs. Anything else is a bug rather than a
#: business decision, and is refused loudly instead of half-applied.
TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.WAITING: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.IN_PROGRESS: frozenset(
        {TaskStatus.PAUSED, TaskStatus.CURING, TaskStatus.FOR_QC, TaskStatus.CANCELLED}
    ),
    TaskStatus.PAUSED: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    # Curing ends by the clock, not by a person, and what follows is inspection.
    TaskStatus.CURING: frozenset({TaskStatus.FOR_QC, TaskStatus.CANCELLED}),
    TaskStatus.FOR_QC: frozenset({TaskStatus.DONE, TaskStatus.RETURNED, TaskStatus.CANCELLED}),
    # A rework re-enters the same task rather than creating a second one, so the
    # attempts stay attached to the batch the customer ordered — the same rule
    # `production` applies to a failed print.
    TaskStatus.RETURNED: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: TaskStatus, target: TaskStatus) -> None:
    if not can_transition(current, target):
        raise DomainRuleViolationError(
            "error.postproduction.invalid_transition",
            current=current.value,
            target=target.value,
        )


class Urgency(StrEnum):
    """How a task's card is drawn, from the order's promise and nothing else.

    Derived rather than assigned. A priority somebody sets by hand becomes a
    priority everybody sets by hand, and then the board is sorted by who
    complained most recently rather than by what is actually due.
    """

    LATE = "late"
    SOON = "soon"
    OK = "ok"


#: Inside this many minutes of the promise, a task is `SOON`. Two hours is roughly
#: one long finishing operation plus its inspection — the point past which starting
#: something else first makes the order late.
SOON_MINUTES = 120


def urgency_for(minutes_to_due: Decimal | int | float | None) -> Urgency:
    """Which band a task falls in. ``None`` — no promise recorded — reads as `OK`."""
    if minutes_to_due is None:
        return Urgency.OK
    if minutes_to_due < 0:
        return Urgency.LATE
    if minutes_to_due <= SOON_MINUTES:
        return Urgency.SOON
    return Urgency.OK


#: How much of the norm a trainee is held to. Announced on the screen rather than
#: applied silently: an operator comparing their pace against a colleague's has to
#: be able to see why the two numbers are not the same measurement.
TRAINEE_NORM_FACTOR = Decimal("1.3")


def norm_minutes(base_minutes: Decimal, quantity: int, *, trainee: bool = False) -> Decimal:
    """The norm for a batch of this size.

    Linear in quantity, which is deliberately crude and says so. Real finishing
    has a setup cost that a batch of thirty amortizes and a batch of one does not
    — but a norm claiming more precision than the farm has measured just teaches
    people to game it, and the recorded fact-versus-norm figures are what will
    replace this with something earned.
    """
    total = base_minutes * Decimal(max(1, quantity))
    if trainee:
        total *= TRAINEE_NORM_FACTOR
    return total.quantize(Decimal("0.1"))


def pace_percent(norm: Decimal, actual: Decimal) -> Decimal | None:
    """Norm over fact, as a percentage. Above 100 means faster than the norm.

    ``None`` when nothing has been recorded. A pace figure for zero work is the
    same lie as a 100% success rate for zero prints.
    """
    if actual <= 0 or norm <= 0:
        return None
    return (norm / actual * 100).quantize(Decimal("0.1"))


__all__ = [
    "CURES",
    "SOON_MINUTES",
    "TRAINEE_NORM_FACTOR",
    "TRANSITIONS",
    "OperationKind",
    "TaskStatus",
    "Urgency",
    "assert_transition",
    "can_transition",
    "norm_minutes",
    "pace_percent",
    "urgency_for",
]
