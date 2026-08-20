"""What the post has accumulated: its thirty days, and each packer's shift.

**Nothing here is stored and nothing can be awarded by hand.** Every badge is a
predicate over recorded facts, evaluated when the screen opens — the same rule
`postproduction` states and for the same reason. A badge somebody can grant is a
badge somebody can withhold, and at that point the panel stops being a
measurement and becomes a management tool people work around.

Unmeasured is `None`, never zero. A post that has shipped nothing has not gone a
hundred days without a mistake and does not cost nought roubles a parcel; the
screen prints a dash, and the dash is the honest answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.packaging.models import PackTask
from printorian.contexts.packaging.policies import STATS_DAYS, PackStatus
from printorian.contexts.packaging.schemas import PackMetrics, PackScore
from printorian.contexts.packaging.tara import tara_accuracy
from printorian.contexts.postproduction import Badge, pace_percent
from printorian.core.ids import EntityId

#: Parcels packed, by tier. The kit's «пятьсот заказов» is the top one.
VOLUME_TIERS: tuple[int, ...] = (100, 250, 500)

#: Consecutive clean parcels behind a spotless record, by tier.
CLEAN_TIERS: tuple[int, ...] = (25, 100, 250)

#: Pace thresholds, as a percentage of the instruction's own norm.
PACE_TIERS: tuple[Decimal, ...] = (Decimal(95), Decimal(105), Decimal(115))

#: Share of parcels made before their van left, as a percentage.
CUTOFF_TIERS: tuple[Decimal, ...] = (Decimal(90), Decimal(97), Decimal(100))

#: Wrapped parcels, by tier — the «хрупкий груз» mark.
FRAGILE_TIERS: tuple[int, ...] = (10, 50, 150)


class _Row:
    """One packer's raw totals, before any of it means anything."""

    __slots__ = (
        "cutoffs_met",
        "elapsed",
        "fragile",
        "name",
        "norm",
        "operator_id",
        "packed",
        "short",
    )

    def __init__(self, operator_id: EntityId, name: str) -> None:
        self.operator_id = operator_id
        self.name = name
        self.packed = 0
        self.short = 0
        self.fragile = 0
        self.cutoffs_met = 0
        self.norm = Decimal(0)
        self.elapsed = Decimal(0)


async def metrics(db: AsyncSession, *, now: datetime, days: int = STATS_DAYS) -> PackMetrics:
    """The thirty-day panel. Facts only; nothing here is a target."""
    since = now - timedelta(days=days)
    row = (
        await db.execute(
            select(
                func.count(PackTask.id),
                func.coalesce(func.sum(PackTask.elapsed_minutes), 0),
                func.coalesce(func.sum(PackTask.norm_minutes), 0),
                func.coalesce(func.sum(PackTask.packaging_cost), 0),
            ).where(_finished_between(since, now))
        )
    ).one()
    count = int(row[0] or 0)
    short = await _short_count(db, since)

    return PackMetrics(
        days=days,
        packed=count,
        average_minutes=(Decimal(str(row[1])) / count).quantize(Decimal("0.1")) if count else None,
        tara_accuracy_percent=await tara_accuracy(db, now=now, days=days),
        discrepancies=short,
        # Fed by logistics' returns when that screen lands. Until a shipment can
        # be marked damaged there is nothing to count, and printing a zero would
        # claim a clean record the farm has not actually earned.
        damages=None,
        missed_cutoffs=await _missed_cutoffs(db, since),
        cost_per_parcel=(Decimal(str(row[3])) / count).quantize(Decimal("0.01")) if count else None,
        score=_score(
            packed=count,
            short=short,
            pace=pace_percent(Decimal(str(row[2])), Decimal(str(row[1]))),
        ),
    )


async def scorecards(
    db: AsyncSession, *, now: datetime, names: dict[EntityId, str] | None = None, days: int = 1
) -> list[PackScore]:
    """Everyone who packed in the window, best score first.

    ``days`` defaults to one because the panel is titled «пост сегодня» — this is
    the shift, not the month. ``names`` comes from the delivery layer: this context
    knows an operator's id and not their display name, and reaching into `identity`
    for one would be exactly the coupling the boundary exists to prevent.
    """
    since = now - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                PackTask.operator_id,
                func.count(PackTask.id),
                func.coalesce(func.sum(PackTask.norm_minutes), 0),
                func.coalesce(func.sum(PackTask.elapsed_minutes), 0),
                func.coalesce(func.sum(func.cast(PackTask.wrap_required, Integer)), 0),
            )
            .where(_finished_between(since, now), PackTask.operator_id.is_not(None))
            .group_by(PackTask.operator_id)
        )
    ).all()

    found: list[PackScore] = []
    for operator_id, packed, norm, elapsed, fragile in rows:
        row = _Row(operator_id, (names or {}).get(operator_id, ""))
        row.packed = int(packed or 0)
        row.norm = Decimal(str(norm or 0))
        row.elapsed = Decimal(str(elapsed or 0))
        row.fragile = int(fragile or 0)
        row.short = await _short_count(db, since, operator_id=operator_id)
        row.cutoffs_met = await _cutoffs_met(db, since, operator_id=operator_id)
        found.append(_card(row))
    return sorted(found, key=lambda card: card.score or Decimal(0), reverse=True)


# ------------------------------------------------------------------- pieces


def _finished_between(since: datetime, until: datetime) -> ColumnElement[bool]:
    """The window every figure on both panels is taken over."""
    return (
        PackTask.status.in_((PackStatus.READY, PackStatus.SHIPPED))
        & (PackTask.finished_at >= since)
        & (PackTask.finished_at < until)
    )


async def _short_count(
    db: AsyncSession, since: datetime, *, operator_id: EntityId | None = None
) -> int:
    """Parcels where the completeness check disagreed with the order."""
    query = select(func.count(PackTask.id)).where(
        PackTask.discrepancy_at.is_not(None), PackTask.discrepancy_at >= since
    )
    if operator_id is not None:
        query = query.where(PackTask.operator_id == operator_id)
    return int(await db.scalar(query) or 0)


async def _missed_cutoffs(db: AsyncSession, since: datetime) -> int:
    """Parcels finished after their van had already left."""
    value = await db.scalar(
        select(func.count(PackTask.id)).where(
            PackTask.finished_at.is_not(None),
            PackTask.finished_at >= since,
            PackTask.cutoff_at.is_not(None),
            PackTask.finished_at > PackTask.cutoff_at,
        )
    )
    return int(value or 0)


async def _cutoffs_met(db: AsyncSession, since: datetime, *, operator_id: EntityId) -> int:
    value = await db.scalar(
        select(func.count(PackTask.id)).where(
            PackTask.operator_id == operator_id,
            PackTask.finished_at.is_not(None),
            PackTask.finished_at >= since,
            PackTask.cutoff_at.is_not(None),
            PackTask.finished_at <= PackTask.cutoff_at,
        )
    )
    return int(value or 0)


def _card(row: _Row) -> PackScore:
    pace = pace_percent(row.norm, row.elapsed)
    return PackScore(
        operator_id=row.operator_id,
        operator_name=row.name,
        packed=row.packed,
        average_minutes=(row.elapsed / row.packed).quantize(Decimal("0.1")) if row.packed else None,
        discrepancies=row.short,
        pace_percent=pace,
        score=_score(packed=row.packed, short=row.short, pace=pace),
        badges=_badges(row, pace),
    )


def _score(*, packed: int, short: int, pace: Decimal | None) -> Decimal | None:
    """Pace × completeness × volume, on a ten-point scale.

    Stated on the screen so it can be argued with. The three multiply rather than
    add, for the reason `postproduction` gives: somebody fast who ships short
    parcels is not averagely good, and a sum would let volume buy its way past
    the one figure a customer actually notices.
    """
    if pace is None or packed == 0:
        return None
    completeness = Decimal(max(0, packed - short)) / Decimal(packed)
    # Volume saturates: past the first tier more parcels do not make somebody
    # better, they mean they were on shift more.
    volume = min(Decimal(1), Decimal(packed) / Decimal(VOLUME_TIERS[0]))
    return (min(Decimal("1.2"), pace / 100) * completeness * volume * 10).quantize(Decimal("0.1"))


def _badges(row: _Row, pace: Decimal | None) -> list[Badge]:
    on_time = (Decimal(row.cutoffs_met) / Decimal(row.packed) * 100) if row.packed else None
    return [
        Badge(
            code="badge.packing.volume",
            tier=_tier(Decimal(row.packed), [Decimal(one) for one in VOLUME_TIERS]),
            detail={"packed": str(row.packed), "next": str(_next(row.packed, VOLUME_TIERS))},
        ),
        Badge(
            code="badge.packing.complete",
            # Zero short parcels out of two is not an achievement, which is why
            # the volume is inside the predicate and not only in the detail.
            tier=(
                _tier(Decimal(row.packed), [Decimal(one) for one in CLEAN_TIERS])
                if row.short == 0
                else 0
            ),
            detail={"short": str(row.short), "packed": str(row.packed)},
        ),
        Badge(
            code="badge.packing.pace",
            tier=_tier(pace, list(PACE_TIERS)) if pace is not None else 0,
            detail={"pace": str(pace) if pace is not None else ""},
        ),
        Badge(
            code="badge.packing.cutoffs",
            tier=_tier(on_time, list(CUTOFF_TIERS)) if on_time is not None else 0,
            detail={"met": str(row.cutoffs_met), "packed": str(row.packed)},
        ),
        Badge(
            code="badge.packing.fragile",
            tier=_tier(Decimal(row.fragile), [Decimal(one) for one in FRAGILE_TIERS]),
            detail={"wrapped": str(row.fragile), "next": str(_next(row.fragile, FRAGILE_TIERS))},
        ),
    ]


def _tier(value: Decimal | None, thresholds: list[Decimal]) -> int:
    if value is None:
        return 0
    return sum(1 for threshold in thresholds if value >= threshold)


def _next(value: int, thresholds: tuple[int, ...]) -> int:
    """The next threshold, or the top one once passed.

    Returned so the client can say «31 из 100» — the distance to the next mark is
    what makes an unearned badge worth showing at all.
    """
    return next((one for one in thresholds if value < one), thresholds[-1])


__all__ = [
    "CLEAN_TIERS",
    "CUTOFF_TIERS",
    "FRAGILE_TIERS",
    "PACE_TIERS",
    "VOLUME_TIERS",
    "metrics",
    "scorecards",
]
