"""«Заявки» — the work behind a failure, raised, walked through its steps and closed.

Issue #33's second slice. What is pinned here is the part that a board cannot
show wrongly without somebody losing an afternoon:

* **The lanes are a function of status first and kind second.** A repair being
  worked is «В работе», not «Аварийные», or the same card is in two lanes.
* **A driver-opened failure gets exactly one repair ticket.** The sweep may see
  the same failure on two passes; the second must not raise a second SV number.
* **Closing refuses an unticked step** rather than ticking it for you: «Порядок
  работ» exists to make skipped work visible, and a close that hides it is the
  one thing the list must not do.
* **Elapsed is measured against the clock the caller holds**, from `started_at`
  or — while merely raised — `opened_at`, and it stops at `closed_at`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.service import (
    AddStep,
    FailureOrigin,
    RaiseTicket,
    ServiceDesk,
    TicketDesk,
    TicketKind,
    TicketStatus,
    TicketView,
    lanes_of,
)
from printorian.core.clock import FixedClock
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import new_id
from tests.factories import ensure_user
from tests.unit._measure_support import HOUR
from tests.unit._service_support import a_failure, a_printer


@pytest.fixture
def desk(db_session: AsyncSession, clock: FixedClock) -> ServiceDesk:
    return ServiceDesk(db_session, clock)


@pytest.fixture
def tickets(db_session: AsyncSession, clock: FixedClock) -> TicketDesk:
    return TicketDesk(db_session, clock)


def a_repair(**overrides: object) -> RaiseTicket:
    base: dict[str, object] = {
        "kind": TicketKind.REPAIR,
        "title": "замена сопла",
        "steps": [
            AddStep(title="Остановить печать, снять задание", norm_minutes=2),
            AddStep(title="Извлечь остаток филамента из тракта", norm_minutes=10),
        ],
    }
    return RaiseTicket(**{**base, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------------- the lanes


def test_status_wins_over_kind_and_every_open_kind_has_exactly_one_lane() -> None:
    """Pure. A repair being worked is «В работе»; each open kind lands in one lane."""

    def view(kind: TicketKind, status: TicketStatus) -> TicketView:
        return TicketView(
            id=new_id(),
            number=f"SV-{kind.value}",
            kind=kind,
            status=status,
            origin=FailureOrigin.PERSON,
            opened_at=HOUR,
        )

    board = lanes_of(
        [
            view(TicketKind.REPAIR, TicketStatus.OPEN),
            view(TicketKind.INSTALL, TicketStatus.OPEN),
            view(TicketKind.MAINTENANCE, TicketStatus.OPEN),
            view(TicketKind.MATERIAL_LOAD, TicketStatus.OPEN),
            view(TicketKind.MOVE, TicketStatus.OPEN),
            view(TicketKind.REPAIR, TicketStatus.IN_PROGRESS),
            view(TicketKind.MOVE, TicketStatus.CLOSED),
        ]
    )

    assert [t.number for t in board.emergency] == ["SV-repair"]
    assert [t.number for t in board.planned] == ["SV-install", "SV-maintenance"]
    assert [t.number for t in board.logistics] == ["SV-material_load", "SV-move"]
    assert [t.number for t in board.in_progress] == ["SV-repair"]
    assert [t.number for t in board.closed] == ["SV-move"]


# ------------------------------------------------------------- raising


async def test_a_raised_ticket_is_numbered_open_and_carries_its_steps_in_order(
    tickets: TicketDesk, clock: FixedClock
) -> None:
    clock.set(HOUR)
    first = await tickets.raise_ticket(a_repair(), by=None)
    second = await tickets.raise_ticket(a_repair(title="чистка сопла"), by=None)

    assert first.number.startswith("SV-") and second.number > first.number
    assert first.status is TicketStatus.OPEN
    assert first.origin is FailureOrigin.PERSON
    assert first.opened_at == HOUR
    assert [(s.position, s.title, s.norm_minutes) for s in first.steps] == [
        (1, "Остановить печать, снять задание", 2),
        (2, "Извлечь остаток филамента из тракта", 10),
    ]
    assert first.steps_done == 0
    assert first.elapsed_seconds == 0


async def test_a_driver_failure_gets_one_repair_ticket_dated_to_the_failure(
    desk: ServiceDesk, tickets: TicketDesk, db_session: AsyncSession, clock: FixedClock
) -> None:
    """Dated to `detected_at`, not the sweep's clock; and only once."""
    printer = await a_printer(db_session)
    failure_id = await a_failure(
        desk, printer, at=HOUR, origin=FailureOrigin.DRIVER, error_code="bambu.print_error.0300"
    )
    failure = next(f for f in await desk.open_for([printer]) if f.id == failure_id)
    clock.set(HOUR + timedelta(minutes=41))

    ticket = await tickets.open_for_failure(failure)
    again = await tickets.open_for_failure(failure)

    assert ticket is not None
    assert again is None
    assert ticket.kind is TicketKind.REPAIR
    assert ticket.origin is FailureOrigin.DRIVER
    assert ticket.failure_id == failure_id
    assert ticket.printer_id == printer
    assert ticket.title == ""
    assert ticket.opened_at == HOUR
    # «ПРОСТОЙ 41 М»: since the machine stopped, as the machine reported it.
    assert ticket.elapsed_seconds == 41 * 60
    board = await tickets.board(closed_since=HOUR)
    assert [t.id for t in board.emergency] == [ticket.id]


# ------------------------------------------------------------- working


async def test_starting_moves_the_ticket_to_in_progress_and_hands_it_to_whoever_started(
    tickets: TicketDesk, clock: FixedClock, db_session: AsyncSession
) -> None:
    clock.set(HOUR)
    ticket = await tickets.raise_ticket(a_repair(), by=None)
    worker = new_id()
    await ensure_user(db_session, worker)
    clock.set(HOUR + timedelta(minutes=5))

    started = await tickets.start(ticket.id, by=worker)
    clock.set(HOUR + timedelta(minutes=33))
    read = await tickets.get(ticket.id)

    assert started.status is TicketStatus.IN_PROGRESS
    assert started.started_at == HOUR + timedelta(minutes=5)
    assert started.assignee_id == worker
    # «28 М ИЗ 2 Ч»: elapsed since the work started, not since it was raised.
    assert read.elapsed_seconds == 28 * 60
    board = await tickets.board(closed_since=HOUR)
    assert [t.id for t in board.in_progress] == [ticket.id]
    assert board.emergency == []


async def test_ticking_a_step_starts_a_merely_raised_ticket(
    tickets: TicketDesk, clock: FixedClock, db_session: AsyncSession
) -> None:
    clock.set(HOUR)
    ticket = await tickets.raise_ticket(a_repair(), by=None)
    worker = new_id()
    await ensure_user(db_session, worker)

    done = await tickets.complete_step(ticket.id, 1, by=worker)

    assert done.status is TicketStatus.IN_PROGRESS
    assert done.steps_done == 1
    assert done.steps[0].done_at == HOUR
    assert done.steps[0].done_by == worker
    assert done.steps[1].done_at is None


async def test_an_unknown_step_is_a_404_not_a_silent_no_op(tickets: TicketDesk) -> None:
    ticket = await tickets.raise_ticket(a_repair(), by=None)
    with pytest.raises(NotFoundError) as raised:
        await tickets.complete_step(ticket.id, 9, by=None)
    assert raised.value.code == "error.service.step_not_found"
    assert raised.value.details["position"] == 9


async def test_a_step_added_later_goes_after_the_last_one(tickets: TicketDesk) -> None:
    ticket = await tickets.raise_ticket(a_repair(), by=None)

    extended = await tickets.add_step(ticket.id, AddStep(title="Прогнать тест", norm_minutes=8))

    assert [s.position for s in extended.steps] == [1, 2, 3]
    assert extended.steps[2].title == "Прогнать тест"


# ------------------------------------------------------------- closing


async def test_closing_refuses_while_a_step_is_unticked_and_names_it(
    tickets: TicketDesk,
) -> None:
    ticket = await tickets.raise_ticket(a_repair(), by=None)
    await tickets.complete_step(ticket.id, 1, by=None)

    with pytest.raises(ValidationError) as raised:
        await tickets.close(ticket.id, by=None)

    assert raised.value.code == "error.service.steps_pending"
    assert raised.value.details["positions"] == [2]


async def test_closing_stamps_the_end_and_freezes_elapsed(
    tickets: TicketDesk, clock: FixedClock, db_session: AsyncSession
) -> None:
    clock.set(HOUR)
    ticket = await tickets.raise_ticket(a_repair(steps=[]), by=None)
    clock.set(HOUR + timedelta(minutes=12))

    worker = new_id()
    await ensure_user(db_session, worker)
    closed = await tickets.close(ticket.id, by=worker)
    clock.set(HOUR + timedelta(hours=5))
    read = await tickets.get(ticket.id)

    assert closed.status is TicketStatus.CLOSED
    assert closed.closed_at == HOUR + timedelta(minutes=12)
    # Never started by hand: nobody measured when the work began, so nothing
    # claims to. Elapsed is the span the ticket was open.
    assert closed.started_at is None
    assert read.elapsed_seconds == 12 * 60
    assert closed.assignee_id == worker


async def test_a_closed_ticket_refuses_further_work(tickets: TicketDesk) -> None:
    ticket = await tickets.raise_ticket(a_repair(steps=[]), by=None)
    await tickets.close(ticket.id, by=None)

    for act in (
        lambda: tickets.start(ticket.id, by=None),
        lambda: tickets.add_step(ticket.id, AddStep(title="ещё")),
        lambda: tickets.assign(ticket.id, new_id()),
    ):
        with pytest.raises(ValidationError) as raised:
            await act()
        assert raised.value.code == "error.service.ticket_closed"


async def test_the_closed_lane_is_bounded_by_time_and_newest_first(
    tickets: TicketDesk, clock: FixedClock
) -> None:
    clock.set(HOUR)
    old = await tickets.raise_ticket(a_repair(steps=[], title="старая"), by=None)
    await tickets.close(old.id, by=None)
    clock.set(HOUR + timedelta(hours=30))
    recent = await tickets.raise_ticket(a_repair(steps=[], title="недавняя"), by=None)
    await tickets.close(recent.id, by=None)
    clock.set(HOUR + timedelta(hours=31))
    newest = await tickets.raise_ticket(a_repair(steps=[], title="новейшая"), by=None)
    await tickets.close(newest.id, by=None)

    board = await tickets.board(closed_since=HOUR + timedelta(hours=24))

    assert [t.title for t in board.closed] == ["новейшая", "недавняя"]


async def test_an_unknown_ticket_is_a_404(tickets: TicketDesk) -> None:
    with pytest.raises(NotFoundError) as raised:
        await tickets.get(new_id())
    assert raised.value.code == "error.service.ticket_not_found"
