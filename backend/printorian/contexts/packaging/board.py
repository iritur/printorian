"""The board, the shift tiles and the pickup list.

Reads only, for the same reason `postproduction/board.py` is: bounded aggregate
queries with no clock to advance, no bus and no transaction of their own.

**The board is ordered by the van, not by arrival.** Everything going on the
19:30 pickup is due at 19:30 whatever time it was inspected, and a queue sorted
by when work landed is a queue that leaves the rush parcel until last on exactly
the days it matters.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.packaging.models import PackTask, Tara
from printorian.contexts.packaging.policies import (
    SOON_MINUTES,
    STATS_DAYS,
    Dims,
    PackStatus,
)
from printorian.contexts.packaging.schemas import PackColumn, PackKpi, PackView, PickupView
from printorian.contexts.packaging.tara import enclosures, recommend
from printorian.contexts.packaging.views import view_of
from printorian.contexts.postproduction import pace_percent
from printorian.core.ids import EntityId

#: Columns, left to right, as the design draws them. `CHECKED` first because it
#: is the only one a packer acts on unprompted.
COLUMNS: tuple[PackStatus, ...] = (
    PackStatus.CHECKED,
    PackStatus.PACKING,
    PackStatus.HELD,
    PackStatus.READY,
)

#: Which columns count as work still owed. `READY` is deliberately excluded: the
#: parcel is made, and counting it as queued would make a good afternoon look
#: like a backlog.
OPEN: tuple[PackStatus, ...] = (PackStatus.CHECKED, PackStatus.PACKING, PackStatus.HELD)

#: Most cards drawn. A post with more open parcels than this has a backlog no
#: board can usefully render.
BOARD_LIMIT = 200


async def board_columns(db: AsyncSession, *, now: datetime) -> list[PackColumn]:
    """Every open parcel, in its column, soonest van first."""
    tasks = list(
        await db.scalars(
            select(PackTask)
            .where(PackTask.status.in_(COLUMNS))
            .options(selectinload(PackTask.steps))
            # Nulls last: a parcel with no van booked is not thereby the most
            # urgent thing on the bench, which an unqualified ascending sort
            # would claim.
            # `id` last: three terms and it still tied. A day's parcels are
            # raised together, so they share both the cutoff and the
            # transaction's `now()`, and `BOARD_LIMIT` then cuts the bench in a
            # different place each time it is read (`core.pagination`).
            .order_by(
                PackTask.cutoff_at.is_(None),
                PackTask.cutoff_at,
                PackTask.created_at,
                PackTask.id,
            )
            .limit(BOARD_LIMIT)
        )
    )
    candidates = await enclosures(db)
    by_id: dict[EntityId, Tara] = {tara.id: tara for tara in candidates}

    grouped: dict[PackStatus, list[PackView]] = {status: [] for status in COLUMNS}
    for task in tasks:
        suggested = recommend(candidates, Dims(task.length_mm, task.width_mm, task.height_mm))
        chosen = by_id.get(task.tara_id) if task.tara_id is not None else None
        grouped[task.status].append(view_of(task, now=now, suggested=suggested, chosen=chosen))
    return [PackColumn(status=status, tasks=grouped[status]) for status in COLUMNS]


async def next_cutoff(db: AsyncSession, *, now: datetime) -> datetime | None:
    """The soonest van still to come that has something waiting for it.

    The header counts down to this. A cutoff already past is not offered: the
    parcels that missed it are visible as urgent on the board, and a countdown
    running backwards teaches people to stop reading it.
    """
    return await db.scalar(
        select(func.min(PackTask.cutoff_at)).where(
            PackTask.status.in_(OPEN), PackTask.cutoff_at.is_not(None), PackTask.cutoff_at >= now
        )
    )


async def pickups(db: AsyncSession, *, now: datetime) -> list[PickupView]:
    """What is going out on each van today, soonest first."""
    rows = (
        await db.execute(
            select(
                PackTask.delivery_method,
                PackTask.carrier_code,
                PackTask.cutoff_at,
                func.count(PackTask.id),
            )
            .where(
                PackTask.status.in_((*OPEN, PackStatus.READY)),
                PackTask.cutoff_at.is_not(None),
                PackTask.cutoff_at >= now,
                PackTask.cutoff_at < _midnight(now) + timedelta(days=1),
            )
            .group_by(PackTask.delivery_method, PackTask.carrier_code, PackTask.cutoff_at)
            # The whole group key, not just the time: carriers collecting at the
            # same cutoff are the normal case here, and there is no `id` to fall
            # back on in a grouped result (`core.pagination`).
            .order_by(PackTask.cutoff_at, PackTask.delivery_method, PackTask.carrier_code)
        )
    ).all()
    return [
        PickupView(method=method, carrier_code=carrier, at=at, parcels=int(count or 0))
        for method, carrier, at, count in rows
    ]


async def shift_kpi(db: AsyncSession, *, now: datetime, days: int = STATS_DAYS) -> PackKpi:
    """The four tiles: what is waiting, what got made, how clean, how expensive."""
    midnight = _midnight(now)
    since = now - timedelta(days=days)

    by_method = await _open_by_method(db)
    packed_today = await _packed_between(db, midnight, now)
    packed_yesterday = await _packed_between(db, midnight - timedelta(days=1), midnight)
    average, norm, cost = await _window_averages(db, since, now)
    discrepancies = await _discrepancies_since(db, since)

    return PackKpi(
        queued=sum(count for _, count in by_method),
        queued_by_method=by_method,
        urgent=await _urgent(db, now),
        due_before_cutoff=await _due_before(db, midnight + timedelta(days=1)),
        packed_today=packed_today,
        packed_yesterday=packed_yesterday,
        average_minutes=average,
        norm_minutes=norm,
        pace_percent=pace_percent(norm, average) if norm and average else None,
        days_without_discrepancy=await _clean_days(db, now),
        discrepancies=discrepancies,
        cost_per_parcel=cost,
    )


# ------------------------------------------------------------------- pieces


def _midnight(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


async def _open_by_method(db: AsyncSession) -> list[tuple[str, int]]:
    rows = await db.execute(
        select(PackTask.delivery_method, func.count(PackTask.id))
        .where(PackTask.status.in_(OPEN))
        .group_by(PackTask.delivery_method)
        .order_by(PackTask.delivery_method)
    )
    return [(method, int(count or 0)) for method, count in rows.all()]


async def _urgent(db: AsyncSession, now: datetime) -> int:
    """Open parcels inside the warning band, or whose van has already gone."""
    cutoff = now + timedelta(minutes=SOON_MINUTES)
    value = await db.scalar(
        select(func.count(PackTask.id)).where(
            PackTask.status.in_(OPEN), PackTask.cutoff_at.is_not(None), PackTask.cutoff_at <= cutoff
        )
    )
    return int(value or 0)


async def _due_before(db: AsyncSession, moment: datetime) -> int:
    value = await db.scalar(
        select(func.count(PackTask.id)).where(
            PackTask.status.in_(OPEN), PackTask.cutoff_at.is_not(None), PackTask.cutoff_at < moment
        )
    )
    return int(value or 0)


async def _packed_between(db: AsyncSession, start: datetime, end: datetime) -> int:
    value = await db.scalar(
        select(func.count(PackTask.id)).where(
            PackTask.status.in_((PackStatus.READY, PackStatus.SHIPPED)),
            PackTask.finished_at >= start,
            PackTask.finished_at < end,
        )
    )
    return int(value or 0)


async def _window_averages(
    db: AsyncSession, since: datetime, until: datetime
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Mean minutes, mean norm and mean packing cost over the window.

    All three or none: a pace figure needs both halves, and a cost per parcel over
    no parcels is not zero roubles — it is no measurement.
    """
    row = (
        await db.execute(
            select(
                func.count(PackTask.id),
                func.coalesce(func.sum(PackTask.elapsed_minutes), 0),
                func.coalesce(func.sum(PackTask.norm_minutes), 0),
                func.coalesce(func.sum(PackTask.packaging_cost), 0),
            ).where(
                PackTask.status.in_((PackStatus.READY, PackStatus.SHIPPED)),
                PackTask.finished_at >= since,
                PackTask.finished_at < until,
            )
        )
    ).one()
    count = int(row[0] or 0)
    if count == 0:
        return None, None, None
    divisor = Decimal(count)
    return (
        (Decimal(str(row[1])) / divisor).quantize(Decimal("0.1")),
        (Decimal(str(row[2])) / divisor).quantize(Decimal("0.1")),
        (Decimal(str(row[3])) / divisor).quantize(Decimal("0.01")),
    )


async def _discrepancies_since(db: AsyncSession, since: datetime) -> int:
    value = await db.scalar(
        select(func.count(PackTask.id)).where(
            PackTask.discrepancy_at.is_not(None), PackTask.discrepancy_at >= since
        )
    )
    return int(value or 0)


async def _clean_days(db: AsyncSession, now: datetime) -> int | None:
    """Days since the last recorded discrepancy.

    ``None`` when nothing has ever shipped: a post with no history has not gone
    sixty-two days without a mistake, it has simply not been measured, and the
    difference is the whole credibility of the figure.
    """
    shipped = await db.scalar(
        select(func.count(PackTask.id)).where(PackTask.status == PackStatus.SHIPPED)
    )
    if not shipped:
        return None
    last = await db.scalar(
        select(func.max(PackTask.discrepancy_at)).where(PackTask.discrepancy_at.is_not(None))
    )
    first = await db.scalar(select(func.min(PackTask.created_at)))
    since = last or first
    if since is None:  # pragma: no cover - there is at least one shipped parcel
        return None
    return max(0, (now - since).days)


__all__ = [
    "BOARD_LIMIT",
    "COLUMNS",
    "OPEN",
    "board_columns",
    "next_cutoff",
    "pickups",
    "shift_kpi",
]
