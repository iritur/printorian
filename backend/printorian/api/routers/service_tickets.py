"""«Заявки» over HTTP: the board, one ticket, and the four things a person does to one.

A router of its own under `/service/tickets`, beside `service.py`, for the reason
that file gives for not living under `/printers`: this is work, not a registry. It
takes the same two permissions — reading is `VIEW_PRODUCTION`, writing is
`OPERATE_PRINTER`, which the operator role already holds. Nothing here needs a new
`Permission` member: raising a ticket, ticking a step and closing it are the acts
of the person at the machine, and gating them behind a manager would turn the
board into a form nobody fills in.

**Minutes and counts leave here; rubles do not.** The kit's «Последствия» panel
prices a ticket, and every input to that multiplication is one import away.
`service/schemas.py` opens with the rule, and this router is where it would break.

The board carries the printer names beside the tickets rather than making the
client fetch `/printers` to label a card: a ticket about a retired machine must
still name it, and `include_inactive=True` is the only read that does.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from printorian.api.deps import AppClock, CurrentActor, DbSession, Fleet, requires
from printorian.contexts.identity import Permission
from printorian.contexts.service import (
    AddStep,
    AssignTicket,
    RaiseTicket,
    TicketBoard,
    TicketDesk,
    TicketView,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/service/tickets",
    tags=["service"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

_OPERATE = Depends(requires(Permission.OPERATE_PRINTER))


def get_ticket_desk(db: DbSession, clock: AppClock) -> TicketDesk:
    return TicketDesk(db, clock)


#: Declared here rather than in `api/deps.py`, which sits at the 400-line gate;
#: this router is the only reader, so the seam is honest rather than a dodge.
TicketDeskDep = Annotated[TicketDesk, Depends(get_ticket_desk)]

#: «Закрыто сегодня», by default: what closed in the last day. A window rather
#: than a calendar day because the farm's day and the server's do not start at
#: the same hour, and the setting that says which is a different slice.
_CLOSED_WINDOW = timedelta(hours=24)


class PrinterLabel(BaseModel):
    id: EntityId
    name: str
    is_active: bool


class TicketBoardView(BaseModel):
    """The board plus the names it needs to label its cards."""

    board: TicketBoard
    printers: list[PrinterLabel] = Field(default_factory=list)
    at: datetime


@router.get("")
async def board(
    tickets: TicketDeskDep,
    fleet: Fleet,
    clock: AppClock,
    closed_since: Annotated[
        datetime | None,
        Query(description="Closed-lane cut-off, tz-aware. Defaults to a day ago."),
    ] = None,
) -> TicketBoardView:
    """The five lanes, read against one instant."""
    now = clock.now()
    table = await fleet.table(include_inactive=True)
    return TicketBoardView(
        board=await tickets.board(closed_since=closed_since or now - _CLOSED_WINDOW),
        printers=[
            PrinterLabel(id=row.id, name=row.name, is_active=row.is_active) for row in table.rows
        ],
        at=now,
    )


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_OPERATE])
async def raise_ticket(
    data: RaiseTicket, tickets: TicketDeskDep, actor: CurrentActor
) -> TicketView:
    """«Создать заявку» — a person raising work, with its steps if they know them."""
    return await tickets.raise_ticket(data, by=actor.user_id)


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: EntityId, tickets: TicketDeskDep) -> TicketView:
    """One ticket in full. An unknown id is a 404, not an empty ticket."""
    return await tickets.get(ticket_id)


@router.post("/{ticket_id}/start", dependencies=[_OPERATE])
async def start(ticket_id: EntityId, tickets: TicketDeskDep, actor: CurrentActor) -> TicketView:
    """Pick the work up. Unassigned tickets go to whoever starts them."""
    return await tickets.start(ticket_id, by=actor.user_id)


@router.post("/{ticket_id}/assign", dependencies=[_OPERATE])
async def assign(ticket_id: EntityId, data: AssignTicket, tickets: TicketDeskDep) -> TicketView:
    return await tickets.assign(ticket_id, data.assignee_id)


@router.post("/{ticket_id}/steps", status_code=status.HTTP_201_CREATED, dependencies=[_OPERATE])
async def add_step(ticket_id: EntityId, data: AddStep, tickets: TicketDeskDep) -> TicketView:
    """Append a line to «Порядок работ»."""
    return await tickets.add_step(ticket_id, data)


@router.post("/{ticket_id}/steps/{position}/done", dependencies=[_OPERATE])
async def complete_step(
    ticket_id: EntityId, position: int, tickets: TicketDeskDep, actor: CurrentActor
) -> TicketView:
    """Tick one step. Ticking a step on a merely raised ticket starts it."""
    return await tickets.complete_step(ticket_id, position, by=actor.user_id)


@router.post("/{ticket_id}/close", dependencies=[_OPERATE])
async def close(ticket_id: EntityId, tickets: TicketDeskDep, actor: CurrentActor) -> TicketView:
    """Done. Refused while a step is unticked — `error.service.steps_pending`."""
    return await tickets.close(ticket_id, by=actor.user_id)
