"""Events published by post-production.

These are what the floor display reacts to and what the customer's cabinet turns
into pipeline progress. `TaskRaised` in particular is the scenario's step 10 —
the print finished, and somebody has to be told.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from printorian.contexts.postproduction.policies import OperationKind, TaskStatus
from printorian.core.events import Event
from printorian.core.ids import EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskRaised(Event):
    """A finished print has become work for a person.

    Carries the operation so a floor display can route it to the right post
    without another round trip.
    """

    name: ClassVar[str] = "postproduction.task_raised"

    task_id: EntityId
    order_id: EntityId
    number: str
    kind: OperationKind
    quantity: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskStatusChanged(Event):
    """A task moved. The board's one reason to refetch."""

    name: ClassVar[str] = "postproduction.task_status_changed"

    task_id: EntityId
    order_id: EntityId
    number: str
    from_status: TaskStatus
    to_status: TaskStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskReturned(Event):
    """Quality control sent a batch back.

    Distinct from an ordinary status change because it is the one transition that
    costs money and is worth counting: the defect code is the input to every
    return-rate figure on the screen.
    """

    name: ClassVar[str] = "postproduction.task_returned"

    task_id: EntityId
    order_id: EntityId
    number: str
    defect_code: str
    attempt: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskCompleted(Event):
    """The batch passed inspection and is ready to be packed."""

    name: ClassVar[str] = "postproduction.task_completed"

    task_id: EntityId
    order_id: EntityId
    number: str
    kind: OperationKind


__all__ = ["TaskCompleted", "TaskRaised", "TaskReturned", "TaskStatusChanged"]
