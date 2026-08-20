"""The board, the shift tiles and the operations table.

Reads only. Separate from the service for the same reason `production/reads.py`
is: these are bounded aggregate queries with no clock-advancing, no bus and no
transaction of their own.

**The board is ordered by the promise, not by arrival.** A queue sorted by when
work landed is a queue that ships the wrong order first on every busy day, and
"first in, first out" is only fair when everything is due at the same time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.postproduction.models import Operation, Task
from printorian.contexts.postproduction.policies import (
    SOON_MINUTES,
    OperationKind,
    TaskStatus,
    pace_percent,
    urgency_for,
)
from printorian.contexts.postproduction.schemas import (
    Column,
    OperationStat,
    ShiftKpi,
    StepView,
    TaskView,
)

#: Columns, left to right, in the order the design draws them. `WAITING` first
#: because it is the only one an operator acts on unprompted.
COLUMNS: tuple[TaskStatus, ...] = (
    TaskStatus.WAITING,
    TaskStatus.IN_PROGRESS,
    TaskStatus.PAUSED,
    TaskStatus.CURING,
    TaskStatus.FOR_QC,
    TaskStatus.RETURNED,
)

#: How far back the operations table and the pace figures look.
STATS_DAYS = 30

#: Most cards drawn on the board. A post with more open work than this has a
#: backlog no board can usefully render.
BOARD_LIMIT = 200

#: Days on the output sparkline.
OUTPUT_DAYS = 14


async def board_columns(db: AsyncSession, *, now: datetime) -> list[Column]:
    """Every open task, in its column, promise first."""
    tasks = list(
        await db.scalars(
            select(Task)
            .where(Task.status.in_(COLUMNS))
            .options(selectinload(Task.steps), selectinload(Task.operation))
            # Nulls last: a task with no promise recorded is not thereby the most
            # urgent thing in the shop, which is what an unqualified ascending
            # sort would claim.
            .order_by(Task.due_at.is_(None), Task.due_at, Task.created_at)
            .limit(BOARD_LIMIT)
        )
    )
    grouped: dict[TaskStatus, list[TaskView]] = {status: [] for status in COLUMNS}
    for task in tasks:
        grouped[task.status].append(view_of(task, now=now))
    return [Column(status=status, tasks=grouped[status]) for status in COLUMNS]


def view_of(task: Task, *, now: datetime) -> TaskView:
    """One card. Mirrors the service's own view, from an eagerly loaded row."""
    live = task.elapsed_minutes
    if task.running_since is not None:
        live += _minutes(now - task.running_since)
    to_due = _minutes(task.due_at - now) if task.due_at is not None else None
    steps = [StepView.model_validate(step) for step in task.steps]
    remaining = sum((step.norm_minutes for step in steps if step.done_at is None), Decimal(0))

    return TaskView(
        id=task.id,
        number=task.number,
        status=task.status,
        kind=task.operation.kind,
        order_id=task.order_id,
        model_name=task.model_name,
        material_code=task.material_code,
        colors=list(task.colors),
        printer_id=task.printer_id,
        quantity=task.quantity,
        due_at=task.due_at,
        urgency=urgency_for(to_due),
        minutes_to_due=to_due,
        norm_minutes=task.norm_minutes,
        elapsed_minutes=live.quantize(Decimal("0.01")),
        instruction_version=task.instruction_version,
        pace_percent=pace_percent(task.norm_minutes, live),
        projected_minutes=(live + remaining).quantize(Decimal("0.1")),
        operator_id=task.operator_id,
        started_at=task.started_at,
        finished_at=task.finished_at,
        cure_until=task.cure_until,
        attempt=task.attempt,
        defect_code=task.defect_code,
        defect_note=task.defect_note,
        steps=steps,
    )


async def shift_kpi(db: AsyncSession, *, now: datetime) -> ShiftKpi:
    """The four tiles: what is waiting, what got done, how good, how fast."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    queued = await _open_by_kind(db)
    today = await _completed_between(db, midnight, now)
    yesterday = await _completed_between(db, midnight - timedelta(days=1), midnight)

    since = now - timedelta(days=STATS_DAYS)
    returns = await _returns_since(db, since)
    completed = await _completed_between(db, since, now)
    shop = await _shop_pace(db, since, now)

    return ShiftKpi(
        queued=sum(count for _, count in queued),
        queued_by_kind=queued,
        urgent=await _urgent(db, now),
        completed_today=today,
        completed_yesterday=yesterday,
        # Quality is returns against completions over the window, not the shift: a
        # single shift rarely holds enough finished work for the percentage to say
        # anything, and a figure that swings twenty points on one return is noise.
        quality_percent=(
            ((Decimal(completed - returns) / Decimal(completed)) * 100).quantize(Decimal("0.1"))
            if completed
            else None
        ),
        returns=returns,
        pace_percent=shop,
        shop_pace_percent=shop,
    )


async def operation_stats(db: AsyncSession, *, now: datetime) -> list[OperationStat]:
    """Fact against norm, per operation, over the window.

    This is the table that tells the farm its norms are wrong. Painting running
    at 89% of norm month after month is either a training problem or a norm
    problem, and the screen deliberately does not decide which.
    """
    since = now - timedelta(days=STATS_DAYS)
    rows = (
        await db.execute(
            select(
                Operation.kind,
                func.count(Task.id),
                func.coalesce(func.sum(Task.norm_minutes), 0),
                func.coalesce(func.sum(Task.elapsed_minutes), 0),
                func.coalesce(func.sum(Task.attempt - 1), 0),
            )
            .select_from(Task)
            .join(Operation, Operation.id == Task.operation_id)
            .where(Task.status == TaskStatus.DONE, Task.finished_at >= since)
            .group_by(Operation.kind)
        )
    ).all()

    return [
        OperationStat(
            kind=kind,
            completed=int(completed or 0),
            norm_minutes=Decimal(str(norm or 0)),
            actual_minutes=Decimal(str(actual or 0)),
            pace_percent=pace_percent(Decimal(str(norm or 0)), Decimal(str(actual or 0))),
            returns=int(returns or 0),
        )
        for kind, completed, norm, actual, returns in rows
    ]


async def output_by_day(db: AsyncSession, *, now: datetime) -> list[tuple[datetime, int]]:
    """Tasks completed per day. Quiet days are zeroes, not gaps."""
    first = (now - timedelta(days=OUTPUT_DAYS - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day = func.date(Task.finished_at)
    rows = await db.execute(
        select(day, func.count(Task.id))
        .where(Task.status == TaskStatus.DONE, Task.finished_at >= first)
        .group_by(day)
    )
    done = {str(key): int(count or 0) for key, count in rows.all()}
    return [
        (
            first + timedelta(days=offset),
            done.get((first + timedelta(days=offset)).date().isoformat(), 0),
        )
        for offset in range(OUTPUT_DAYS)
    ]


# ------------------------------------------------------------------- pieces


def _minutes(delta: timedelta) -> Decimal:
    return Decimal(str(delta / timedelta(minutes=1))).quantize(Decimal("0.1"))


async def _open_by_kind(db: AsyncSession) -> list[tuple[OperationKind, int]]:
    rows = await db.execute(
        select(Operation.kind, func.count(Task.id))
        .select_from(Task)
        .join(Operation, Operation.id == Task.operation_id)
        .where(Task.status.in_(COLUMNS))
        .group_by(Operation.kind)
    )
    return [(kind, int(count or 0)) for kind, count in rows.all()]


async def _urgent(db: AsyncSession, now: datetime) -> int:
    """Open tasks already past their promise or inside the warning band."""
    cutoff = now + timedelta(minutes=SOON_MINUTES)
    value = await db.scalar(
        select(func.count(Task.id)).where(
            Task.status.in_(COLUMNS), Task.due_at.is_not(None), Task.due_at <= cutoff
        )
    )
    return int(value or 0)


async def _completed_between(db: AsyncSession, start: datetime, end: datetime) -> int:
    value = await db.scalar(
        select(func.count(Task.id)).where(
            Task.status == TaskStatus.DONE, Task.finished_at >= start, Task.finished_at < end
        )
    )
    return int(value or 0)


async def _returns_since(db: AsyncSession, since: datetime) -> int:
    """Reworks, counted as attempts beyond the first."""
    value = await db.scalar(
        select(func.coalesce(func.sum(Task.attempt - 1), 0)).where(
            Task.status == TaskStatus.DONE, Task.finished_at >= since
        )
    )
    return int(value or 0)


async def _shop_pace(db: AsyncSession, since: datetime, until: datetime) -> Decimal | None:
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Task.norm_minutes), 0),
                func.coalesce(func.sum(Task.elapsed_minutes), 0),
            ).where(
                Task.status == TaskStatus.DONE,
                Task.finished_at >= since,
                Task.finished_at < until,
            )
        )
    ).one()
    return pace_percent(Decimal(str(row[0] or 0)), Decimal(str(row[1] or 0)))


__all__ = [
    "BOARD_LIMIT",
    "COLUMNS",
    "OUTPUT_DAYS",
    "STATS_DAYS",
    "board_columns",
    "operation_stats",
    "output_by_day",
    "shift_kpi",
    "view_of",
]
