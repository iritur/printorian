"""Post-production over HTTP — an operator's shift.

Two permissions, deliberately different. Everything that moves a task needs
`ADVANCE_POSTPRODUCTION`, which every operator has; the quality-control verdicts
need `RECORD_QC`, which is separate for the obvious reason — the person who did
the work should not be the only person who ever inspects it. Both live on the
operator role today, so this is a boundary drawn before it is enforced by the
role matrix rather than after somebody notices it is missing.

The board is one request for the same reason the dashboard is: the columns, the
tiles, the operations table and the scorecards all describe one instant, and a
client fanning out would show a task in two columns at once.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status

from printorian.api.deps import (
    AppClock,
    CurrentActor,
    DbSession,
    Identity,
    PostProduction,
    requires,
)
from printorian.contexts.identity import Permission
from printorian.contexts.ordering import numbers_for
from printorian.contexts.postproduction import (
    Board,
    Column,
    CompleteStep,
    CreateTask,
    InstructionCatalogue,
    ReportDefect,
    TaskView,
    board_columns,
    operation_stats,
    output_by_day,
    scorecards,
    shift_kpi,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/postproduction",
    tags=["postproduction"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

_ADVANCE = Depends(requires(Permission.ADVANCE_POSTPRODUCTION))
_QC = Depends(requires(Permission.RECORD_QC))


@router.get("/board")
async def board(db: DbSession, clock: AppClock, identity: Identity) -> Board:
    """The whole screen, read against one instant.

    Operator names come from `identity` here rather than from post-production,
    which knows an operator's id and not what they are called. Composition is the
    delivery layer's job — the same join the schedule strip needs for order
    numbers.
    """
    now = clock.now()
    # One name lookup for the whole screen: the cards and the scorecards want the
    # same map, and asking twice would let the two disagree about a person renamed
    # between the queries.
    names = await identity.display_names()
    return Board(
        at=now,
        columns=await _named_columns(db, now=now, names=names),
        kpi=await shift_kpi(db, now=now),
        operations=await operation_stats(db, now=now),
        shift=await scorecards(db, now=now, names=names),
        consumables=await InstructionCatalogue(db).consumables(),
        output_by_day=await output_by_day(db, now=now),
    )


async def _named_columns(
    db: DbSession, *, now: datetime, names: dict[EntityId, str]
) -> list[Column]:
    """The board, with orders and operators named rather than identified.

    Post-production knows which order a batch belongs to and who is on it — and
    not what either is *called*. Reaching into `ordering` or `identity` for a
    label is the coupling the boundary exists to prevent, so resolving it is
    composition, and composition is this layer's job.
    """
    columns = await board_columns(db, now=now)
    cards = [card for column in columns for card in column.tasks]
    numbers = await numbers_for(db, [card.order_id for card in cards])
    for card in cards:
        card.order_number = numbers.get(card.order_id, "")
        if card.operator_id is not None:
            card.operator_name = names.get(card.operator_id, "")
    return columns


@router.post("/tasks", status_code=status.HTTP_201_CREATED, dependencies=[_ADVANCE])
async def raise_task(data: CreateTask, service: PostProduction) -> TaskView:
    """Raise a task by hand.

    Normally the print-finished handler's job. Kept open to the floor because a
    part that came off a machine the farm does not drive — the `manual` driver's
    machines are real machines — still has to reach the post.
    """
    return await service.raise_task(data)


@router.post("/tasks/{task_id}/start", dependencies=[_ADVANCE])
async def start(task_id: EntityId, actor: CurrentActor, service: PostProduction) -> TaskView:
    """Pick a task up. The clock starts here.

    The operator is the caller, never a field in the body: a task attributed to
    somebody else is how a scorecard stops being a measurement.
    """
    return await service.start(task_id, actor.user_id)


@router.post("/tasks/{task_id}/pause", dependencies=[_ADVANCE])
async def pause(task_id: EntityId, service: PostProduction) -> TaskView:
    """Stop the clock without giving the task up."""
    return await service.pause(task_id)


@router.post("/tasks/{task_id}/steps", dependencies=[_ADVANCE])
async def complete_step(task_id: EntityId, data: CompleteStep, service: PostProduction) -> TaskView:
    """Tick one step off. The last one moves the task on by itself."""
    return await service.complete_step(task_id, data.position)


@router.post("/tasks/{task_id}/finish", dependencies=[_ADVANCE])
async def finish(task_id: EntityId, service: PostProduction) -> TaskView:
    """Work done — to drying if the operation cures, to inspection otherwise."""
    return await service.finish(task_id)


@router.post("/tasks/{task_id}/pass", dependencies=[_QC])
async def pass_qc(task_id: EntityId, service: PostProduction) -> TaskView:
    """Passed inspection. The batch is ready to be packed."""
    return await service.pass_qc(task_id)


@router.post("/tasks/{task_id}/return", dependencies=[_QC])
async def return_task(task_id: EntityId, data: ReportDefect, service: PostProduction) -> TaskView:
    """Send a batch back with the reason attached.

    The reason is required. A rework with no recorded defect is invisible to
    every return-rate figure on the screen, which is the same as not having
    recorded it at all.
    """
    return await service.return_task(task_id, data)
