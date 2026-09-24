"""«Заявки» — the work behind a failure, and the work that has nothing to do with one.

Issue #33's second slice. The first landed the failure record — the *measurement*:
when a machine stopped working and when it was seen working again. This is the
*work*: a ticket somebody raises (or the sweep raises from a driver-reported
failure), assigns, walks through its steps and closes. The two are different
rows on purpose. A repair ticket closed by a person is not evidence that the
machine came back; `restored_at` is, and `reliability.py` reads only that.

Three things the kit draws are not here, and each for a measured reason:

* **«Последствия» / «Потеря 2 140 ₽».** Money, and every input to it is one
  import away. It belongs behind `VIEW_FINANCIALS` on a route of its own —
  `schemas.py` opens with the rule — so this desk emits minutes and counts only.
* **«Запчасти на посту».** The inventory context knows filament and nothing else;
  a spare-parts stock drawn from nowhere would be a table of invented zeros.
* **«ЗАЯВКИ ТО СОЗДАЮТСЯ ПО НАРАБОТКЕ».** Raising a maintenance ticket when a
  service operation comes due is the service card's job, and the card already
  says «Ближайшее ТО»; a second timer that opens tickets is a follow-up with its
  own idempotence question, not a line in this module.

What *is* here follows the kit's foot note for the other half: «АВАРИЙНЫЕ — ПО
СОБЫТИЮ ДРАЙВЕРА». `open_for_failure` is what `workers/service.py` calls right
after it records a driver-opened failure, and it is idempotent per failure so a
sweep that has not seen its own commit cannot raise the same repair twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.service.models import (
    TICKET_NUMBER_SEQUENCE,
    ServiceTicket,
    ServiceTicketStep,
)
from printorian.contexts.service.policies import (
    EMERGENCY_KINDS,
    LOGISTICS_KINDS,
    PLANNED_KINDS,
    FailureOrigin,
    TicketKind,
    TicketStatus,
)
from printorian.contexts.service.schemas import (
    AddStep,
    FailureView,
    RaiseTicket,
    TicketBoard,
    TicketStepView,
    TicketView,
)
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import EntityId

_NUMBER_PREFIX = "SV"


def view_of(ticket: ServiceTicket, *, now: datetime) -> TicketView:
    """The ticket as the board reads it, with elapsed time measured against ``now``.

    Elapsed runs from `started_at` when the work has started and from `opened_at`
    while it is only raised — the kit's «ПРОСТОЙ 41 М» on an emergency card is
    time since the failure was noticed, not since somebody picked it up. It stops
    at `closed_at`. ``now`` is a parameter so the view is a pure function of the
    row and the clock the caller holds, and a test can state it in one line.
    """
    started = ticket.started_at or ticket.opened_at
    end = ticket.closed_at or now
    elapsed = max(int((end - started).total_seconds()), 0)
    steps = [TicketStepView.model_validate(step) for step in ticket.steps]
    return TicketView(
        id=ticket.id,
        number=ticket.number,
        kind=ticket.kind,
        status=ticket.status,
        origin=ticket.origin,
        printer_id=ticket.printer_id,
        failure_id=ticket.failure_id,
        title=ticket.title,
        note=ticket.note,
        norm_minutes=ticket.norm_minutes,
        opened_at=ticket.opened_at,
        started_at=ticket.started_at,
        closed_at=ticket.closed_at,
        opened_by=ticket.opened_by,
        assignee_id=ticket.assignee_id,
        elapsed_seconds=elapsed,
        steps=steps,
        steps_done=sum(1 for step in steps if step.done_at is not None),
    )


def lanes_of(tickets: Sequence[TicketView]) -> TicketBoard:
    """Sort tickets into the kit's five lanes. Pure, and the whole rule of the board.

    A ticket is in exactly one lane. Status wins over kind: anything being worked
    is «В работе» whatever it is, and anything closed is in the closed lane. The
    open ones split by kind — `policies.EMERGENCY_KINDS` and friends say which.
    """
    board = TicketBoard()
    for ticket in tickets:
        if ticket.status is TicketStatus.CLOSED:
            board.closed.append(ticket)
        elif ticket.status is TicketStatus.IN_PROGRESS:
            board.in_progress.append(ticket)
        elif ticket.kind in EMERGENCY_KINDS:
            board.emergency.append(ticket)
        elif ticket.kind in PLANNED_KINDS:
            board.planned.append(ticket)
        elif ticket.kind in LOGISTICS_KINDS:
            board.logistics.append(ticket)
    return board


class TicketDesk:
    """Tickets, as the farm raises, works and closes them."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._db = session
        self._clock = clock

    # ------------------------------------------------------------ raising

    async def raise_ticket(self, data: RaiseTicket, *, by: EntityId | None) -> TicketView:
        """A person raising work: «Создать заявку»."""
        now = self._clock.now()
        ticket = ServiceTicket(
            number=await self._next_number(),
            kind=data.kind,
            status=TicketStatus.OPEN,
            origin=FailureOrigin.PERSON,
            printer_id=data.printer_id,
            failure_id=None,
            title=data.title,
            note=data.note,
            norm_minutes=data.norm_minutes,
            opened_at=now,
            opened_by=by,
            assignee_id=data.assignee_id,
        )
        ticket.steps = [
            ServiceTicketStep(
                position=index,
                title=step.title,
                note=step.note,
                norm_minutes=step.norm_minutes,
            )
            for index, step in enumerate(data.steps, start=1)
        ]
        self._db.add(ticket)
        await self._db.flush()
        return view_of(ticket, now=now)

    async def open_for_failure(self, failure: FailureView) -> TicketView | None:
        """The repair ticket a driver-opened failure gets, once.

        Returns ``None`` when the failure already has one — the same idempotence
        the sweep relies on for the failure itself. Raised at the failure's own
        `detected_at`, not at the sweep's clock: the «ПРОСТОЙ» on the card is
        time since the machine stopped, which is a thing the machine reported.
        Nothing here is prose — `title` is empty and the client draws «Сообщил
        драйвер» from `origin` (ADR-0012); the failure's `error_code` is on the
        failure the ticket points at.
        """
        existing = await self._db.scalar(
            select(ServiceTicket.id).where(ServiceTicket.failure_id == failure.id)
        )
        if existing is not None:
            return None
        ticket = ServiceTicket(
            number=await self._next_number(),
            kind=TicketKind.REPAIR,
            status=TicketStatus.OPEN,
            origin=FailureOrigin.DRIVER,
            printer_id=failure.printer_id,
            failure_id=failure.id,
            title="",
            note=None,
            norm_minutes=None,
            opened_at=failure.detected_at,
            opened_by=None,
            assignee_id=None,
        )
        # An empty list, assigned: a collection never touched on a new row is a
        # lazy load the first read would trigger outside the greenlet.
        ticket.steps = []
        self._db.add(ticket)
        await self._db.flush()
        return view_of(ticket, now=self._clock.now())

    # ------------------------------------------------------------ working

    async def assign(self, ticket_id: EntityId, assignee_id: EntityId | None) -> TicketView:
        ticket = await self._load(ticket_id)
        self._refuse_closed(ticket)
        ticket.assignee_id = assignee_id
        await self._db.flush()
        return view_of(ticket, now=self._clock.now())

    async def start(self, ticket_id: EntityId, *, by: EntityId | None) -> TicketView:
        """Pick the work up. Idempotent: starting a started ticket changes nothing."""
        ticket = await self._load(ticket_id)
        self._refuse_closed(ticket)
        now = self._clock.now()
        if ticket.status is TicketStatus.OPEN:
            ticket.status = TicketStatus.IN_PROGRESS
            ticket.started_at = now
            if ticket.assignee_id is None:
                ticket.assignee_id = by
        await self._db.flush()
        return view_of(ticket, now=now)

    async def add_step(self, ticket_id: EntityId, data: AddStep) -> TicketView:
        """Append a line to «Порядок работ» after the last one."""
        ticket = await self._load(ticket_id)
        self._refuse_closed(ticket)
        position = max((step.position for step in ticket.steps), default=0) + 1
        ticket.steps.append(
            ServiceTicketStep(
                position=position,
                title=data.title,
                note=data.note,
                norm_minutes=data.norm_minutes,
            )
        )
        await self._db.flush()
        return view_of(ticket, now=self._clock.now())

    async def complete_step(
        self, ticket_id: EntityId, position: int, *, by: EntityId | None
    ) -> TicketView:
        """Tick one step. Starting the ticket if it was merely raised: a step done
        is work begun, whatever the board said."""
        ticket = await self._load(ticket_id)
        self._refuse_closed(ticket)
        step = next((step for step in ticket.steps if step.position == position), None)
        if step is None:
            raise NotFoundError(
                "error.service.step_not_found", ticket_id=str(ticket_id), position=position
            )
        now = self._clock.now()
        if step.done_at is None:
            step.done_at = now
            step.done_by = by
        if ticket.status is TicketStatus.OPEN:
            ticket.status = TicketStatus.IN_PROGRESS
            ticket.started_at = now
            if ticket.assignee_id is None:
                ticket.assignee_id = by
        await self._db.flush()
        return view_of(ticket, now=now)

    async def close(self, ticket_id: EntityId, *, by: EntityId | None) -> TicketView:
        """Done. Steps left unticked are refused rather than ticked for you.

        A ticket closed with a step nobody did is either a step that was not
        needed — then delete it, which is a decision — or work skipped, which is
        the thing «Порядок работ» exists to make visible. A ticket closed without
        ever being started keeps `started_at` null: nobody measured when the work
        began, and stamping the close time there would make a job that waited
        twelve minutes read as one that took none (ADR-0007). Its elapsed time
        is then the whole span it was open, which is the fact there is.
        """
        ticket = await self._load(ticket_id)
        self._refuse_closed(ticket)
        pending = [step.position for step in ticket.steps if step.done_at is None]
        if pending:
            raise ValidationError(
                "error.service.steps_pending", ticket_id=str(ticket_id), positions=pending
            )
        now = self._clock.now()
        ticket.status = TicketStatus.CLOSED
        ticket.closed_at = now
        if ticket.assignee_id is None:
            ticket.assignee_id = by
        await self._db.flush()
        return view_of(ticket, now=now)

    # ------------------------------------------------------------ reading

    async def get(self, ticket_id: EntityId) -> TicketView:
        return view_of(await self._load(ticket_id), now=self._clock.now())

    async def board(self, *, closed_since: datetime) -> TicketBoard:
        """Everything open, plus what closed since ``closed_since``.

        Open tickets oldest first — the one that has waited longest heads its
        lane — and closed ones newest first, which is how the kit lists «Закрыто
        сегодня». The closed lane is bounded by time rather than by count so a
        busy day is not a lane that hides its own morning.
        """
        now = self._clock.now()
        rows = await self._db.scalars(
            select(ServiceTicket)
            .where(
                (ServiceTicket.status != TicketStatus.CLOSED)
                | (ServiceTicket.closed_at >= closed_since)
            )
            .order_by(ServiceTicket.opened_at)
        )
        views = [view_of(ticket, now=now) for ticket in rows.unique().all()]
        board = lanes_of(views)
        board.closed.sort(key=lambda ticket: ticket.closed_at or now, reverse=True)
        board.closed_since = closed_since
        return board

    # ------------------------------------------------------------ internals

    async def _load(self, ticket_id: EntityId) -> ServiceTicket:
        ticket = await self._db.get(ServiceTicket, ticket_id)
        if ticket is None:
            raise NotFoundError("error.service.ticket_not_found", ticket_id=str(ticket_id))
        return ticket

    @staticmethod
    def _refuse_closed(ticket: ServiceTicket) -> None:
        if ticket.status is TicketStatus.CLOSED:
            raise ValidationError("error.service.ticket_closed", ticket_id=str(ticket.id))

    async def _next_number(self) -> str:
        value = await self._db.scalar(select(TICKET_NUMBER_SEQUENCE.next_value()))
        return f"{_NUMBER_PREFIX}-{int(value or 1):06d}"


__all__ = ["TicketDesk", "lanes_of", "view_of"]
