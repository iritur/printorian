"""An operator's shift.

The claims worth pinning are the ones about *time*, because every figure the shop
is judged on is derived from them and every one of them is easy to get subtly
wrong:

* the clock stops when the operator stops, so a batch left paused overnight did
  not take fourteen hours;
* a step's fact is the time since the previous step, and the steps add up to the
  task;
* a rework re-enters the same task and keeps the time already spent, because
  hiding the first attempt would make the norm look achievable when it was not;
* an instruction republished mid-shift does not rewrite the job somebody is
  halfway through.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.postproduction import (
    CreateOperation,
    CreateStep,
    CreateTask,
    InstructionCatalogue,
    OperationKind,
    PostProductionService,
    ReportDefect,
    TaskStatus,
    board_columns,
    norm_minutes,
    pace_percent,
    urgency_for,
)
from printorian.contexts.postproduction.policies import Urgency
from printorian.core.clock import FixedClock
from printorian.core.errors import DomainRuleViolationError
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from tests.factories import ensure_order, ensure_user

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)

#: Keeps operator emails distinct; `users.email` is unique.
_operators = count(1)

#: Keeps order numbers distinct; `orders.number` is unique.
_orders = count(1)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


async def an_order(db: AsyncSession):
    """A real order row: `postproduction_tasks.order_id` is a real foreign key.

    Minimal — these tests are about the shift, not about ordering. The row exists
    so the key resolves.
    """
    order_id = new_id()
    await ensure_order(db, order_id, number=f"ORD-{next(_orders)}")
    return order_id


async def an_operator(db: AsyncSession):
    """A real user row: `postproduction_tasks.operator_id` is a real foreign key.

    A fresh one each call, because `users.email` is unique and two of these land
    in the same test whenever a task changes hands.
    """
    operator_id = new_id()
    await ensure_user(db, operator_id, email=f"operator-{next(_operators)}@printorian.test")
    return operator_id


async def a_sanding_operation(db: AsyncSession) -> None:
    await InstructionCatalogue(db).define_operation(
        CreateOperation(
            kind=OperationKind.SANDING,
            norm_minutes_per_unit=Decimal(4),
            instruction_version="4.2",
        ),
        [
            CreateStep(position=1, title="Снять поддержки", norm_minutes=Decimal(3)),
            CreateStep(
                position=2,
                title="Черновая шлифовка P240",
                norm_minutes=Decimal(12),
                warning="Две стенки 0.6 мм — не давить.",
            ),
            CreateStep(position=3, title="Чистовая шлифовка P400", norm_minutes=Decimal(14)),
        ],
    )


async def a_task(
    db: AsyncSession, clock: FixedClock, *, quantity: int = 10, due_at: datetime | None = None
):
    service = PostProductionService(db, clock, EventBus())
    await a_sanding_operation(db)
    return service, await service.raise_task(
        CreateTask(
            order_id=await an_order(db),
            kind=OperationKind.SANDING,
            model_name="BRACKET_V4",
            material_code="PETG-CF",
            quantity=quantity,
            due_at=due_at,
        )
    )


# --------------------------------------------------------------- the norms


def test_the_norm_scales_with_the_batch() -> None:
    assert norm_minutes(Decimal(4), 10) == Decimal(40)


def test_a_trainee_is_held_to_a_stated_fraction_of_the_norm() -> None:
    """Announced rather than applied silently.

    Two operators comparing their pace have to be able to see why the two numbers
    are not the same measurement.
    """
    assert norm_minutes(Decimal(10), 1, trainee=True) == Decimal(13)


def test_nothing_recorded_has_no_pace() -> None:
    """A pace figure for zero work is the same lie as 100% success on no prints."""
    assert pace_percent(Decimal(40), Decimal(0)) is None


def test_faster_than_the_norm_reads_above_a_hundred() -> None:
    assert pace_percent(Decimal(40), Decimal(32)) == Decimal("125.0")


def test_a_task_with_no_promise_is_not_thereby_urgent() -> None:
    assert urgency_for(None) is Urgency.OK
    assert urgency_for(-5) is Urgency.LATE
    assert urgency_for(30) is Urgency.SOON
    assert urgency_for(600) is Urgency.OK


# ---------------------------------------------------------------- the clock


async def test_the_clock_stops_when_the_operator_stops(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A batch left paused overnight did not take fourteen hours."""
    service, task = await a_task(db_session, clock)
    await service.start(task.id, await an_operator(db_session))

    clock.advance(timedelta(minutes=20))
    await service.pause(task.id)
    clock.advance(timedelta(hours=14))

    resumed = await service.start(task.id, await an_operator(db_session))
    assert resumed.elapsed_minutes == Decimal(20)


async def test_elapsed_time_keeps_running_while_the_task_is_open(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The screen must not show a frozen figure between step ticks.

    Read through the board, because that is the path the screen actually takes:
    the running stretch is added at render time, and a card that only moved when
    a step was ticked would sit at 00:00 through the longest step of the job.
    """
    service, task = await a_task(db_session, clock)
    await service.start(task.id, await an_operator(db_session))
    clock.advance(timedelta(minutes=7))

    columns = await board_columns(db_session, now=clock.now())
    working = next(column for column in columns if column.status is TaskStatus.IN_PROGRESS)
    assert [card.elapsed_minutes for card in working.tasks] == [Decimal(7)]


async def test_a_steps_fact_is_the_time_since_the_previous_step(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The steps have to add up to the task, or the norms are unfalsifiable."""
    service, task = await a_task(db_session, clock)
    await service.start(task.id, await an_operator(db_session))

    clock.advance(timedelta(minutes=3))
    await service.complete_step(task.id, 1)
    clock.advance(timedelta(minutes=11))
    after = await service.complete_step(task.id, 2)

    facts = {step.position: step.actual_minutes for step in after.steps}
    assert facts[1] == Decimal(3)
    assert facts[2] == Decimal(11)
    assert after.elapsed_minutes == Decimal(14)


async def test_the_last_step_finishes_the_task_by_itself(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Ticking the final box *and* pressing a button is how batches sit forgotten."""
    service, task = await a_task(db_session, clock)
    await service.start(task.id, await an_operator(db_session))
    for position in (1, 2, 3):
        clock.advance(timedelta(minutes=5))
        result = await service.complete_step(task.id, position)

    assert result.status is TaskStatus.FOR_QC


async def test_a_step_cannot_be_ticked_twice(db_session: AsyncSession, clock: FixedClock) -> None:
    service, task = await a_task(db_session, clock)
    await service.start(task.id, await an_operator(db_session))
    await service.complete_step(task.id, 1)

    with pytest.raises(DomainRuleViolationError):
        await service.complete_step(task.id, 1)


async def test_work_cannot_be_recorded_against_a_task_nobody_picked_up(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    service, task = await a_task(db_session, clock)

    with pytest.raises(DomainRuleViolationError):
        await service.complete_step(task.id, 1)


# ------------------------------------------------------------------- drying


async def test_an_operation_that_cures_goes_to_dry_before_inspection(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A part waiting on chemistry is not idle work and must not be picked up."""
    await InstructionCatalogue(db_session).define_operation(
        CreateOperation(
            kind=OperationKind.PAINTING, norm_minutes_per_unit=Decimal(30), cure_minutes=120
        ),
        [CreateStep(position=1, title="Красить", norm_minutes=Decimal(30))],
    )
    service = PostProductionService(db_session, clock, EventBus())
    task = await service.raise_task(
        CreateTask(order_id=await an_order(db_session), kind=OperationKind.PAINTING, quantity=1)
    )
    await service.start(task.id, await an_operator(db_session))

    drying = await service.complete_step(task.id, 1)

    assert drying.status is TaskStatus.CURING
    assert drying.cure_until == NOW + timedelta(minutes=120)
    assert (await service.cured(task.id)).status is TaskStatus.FOR_QC


# ------------------------------------------------------------------ rework


async def test_a_return_reopens_the_same_task_and_keeps_the_time_spent(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Hiding the first attempt would make the norm look achievable when it was not."""
    service, task = await a_task(db_session, clock)
    await service.start(task.id, await an_operator(db_session))
    for position in (1, 2, 3):
        clock.advance(timedelta(minutes=10))
        await service.complete_step(task.id, position)

    returned = await service.return_task(task.id, ReportDefect(defect_code="defect.paint_run"))

    assert returned.status is TaskStatus.RETURNED
    assert returned.attempt == 2
    assert returned.defect_code == "defect.paint_run"
    assert returned.elapsed_minutes == Decimal(30)
    # The work has to be redone, so the steps are open again.
    assert all(step.done_at is None for step in returned.steps)


async def test_a_passed_batch_is_done(db_session: AsyncSession, clock: FixedClock) -> None:
    service, task = await a_task(db_session, clock)
    await service.start(task.id, await an_operator(db_session))
    for position in (1, 2, 3):
        await service.complete_step(task.id, position)

    assert (await service.pass_qc(task.id)).status is TaskStatus.DONE


async def test_a_task_cannot_skip_inspection(db_session: AsyncSession, clock: FixedClock) -> None:
    """The state machine is the whole truth about how a task may move."""
    service, task = await a_task(db_session, clock)

    with pytest.raises(DomainRuleViolationError):
        await service.pass_qc(task.id)


# ------------------------------------------------------------- instructions


async def test_republishing_an_instruction_does_not_rewrite_an_open_task(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A norm that changed retroactively is a norm nobody trusts."""
    service, task = await a_task(db_session, clock)
    assert task.instruction_version == "4.2"
    assert [step.title for step in task.steps][1] == "Черновая шлифовка P240"

    await InstructionCatalogue(db_session).define_operation(
        CreateOperation(
            kind=OperationKind.SANDING,
            norm_minutes_per_unit=Decimal(99),
            instruction_version="5.0",
        ),
        [CreateStep(position=1, title="Совершенно другой шаг", norm_minutes=Decimal(1))],
    )

    unchanged = await service.start(task.id, await an_operator(db_session))
    assert unchanged.instruction_version == "4.2"
    assert len(unchanged.steps) == 3
    assert unchanged.norm_minutes == Decimal(40)


async def test_the_urgency_of_a_card_comes_from_the_orders_promise(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Derived, never assigned — a priority somebody sets by hand is a priority
    everybody sets by hand."""
    _, late = await a_task(db_session, clock, due_at=NOW - timedelta(minutes=40))
    assert late.urgency is Urgency.LATE
    assert late.minutes_to_due == Decimal("-40.0")
