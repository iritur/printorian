"""What an operator has accumulated: their pace, their returns, their badges.

**Nothing here is stored and nothing can be awarded by hand.** Every badge is a
predicate over recorded facts, evaluated when the screen is opened. That is not a
performance decision — it is the only version of this a shop floor will accept.
A badge somebody can grant is a badge somebody can withhold, and the moment that
is true the whole panel stops being a measurement and becomes a management tool
people work around.

Unearned badges are returned at tier 0 rather than omitted, so there is something
visible to earn. Tiers are monochrome by design: colour means machine state
everywhere in this system, and a badge is not a state.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.postproduction.models import Operation, Task
from printorian.contexts.postproduction.policies import OperationKind, TaskStatus, pace_percent
from printorian.contexts.postproduction.schemas import Badge, Scorecard
from printorian.core.ids import EntityId

#: The window every figure on the panel is taken over.
SCORE_DAYS = 30

#: Completed-task thresholds for the volume badge, by tier.
VOLUME_TIERS: tuple[int, ...] = (25, 100, 500)

#: Per-operation mastery thresholds.
MASTERY_TIERS: tuple[int, ...] = (25, 100, 250)

#: Pace thresholds, as a percentage of norm.
PACE_TIERS: tuple[Decimal, ...] = (Decimal(95), Decimal(105), Decimal(115))


class _Row:
    """One operator's raw totals, before any of it means anything."""

    __slots__ = ("actual", "by_kind", "completed", "name", "norm", "operator_id", "returns")

    def __init__(self, operator_id: EntityId, name: str) -> None:
        self.operator_id = operator_id
        self.name = name
        self.completed = 0
        self.returns = 0
        self.norm = Decimal(0)
        self.actual = Decimal(0)
        self.by_kind: dict[OperationKind, int] = {}


async def scorecards(
    db: AsyncSession, *, now: datetime, names: dict[EntityId, str] | None = None
) -> list[Scorecard]:
    """Everyone who finished work in the window, best score first.

    ``names`` comes from the delivery layer: this context knows an operator's id
    and not their display name, and reaching into `identity` for one would be the
    coupling the boundary exists to prevent.
    """
    since = now - timedelta(days=SCORE_DAYS)
    rows = (
        await db.execute(
            select(
                Task.operator_id,
                Operation.kind,
                func.count(Task.id),
                func.coalesce(func.sum(Task.attempt - 1), 0),
                func.coalesce(func.sum(Task.norm_minutes), 0),
                func.coalesce(func.sum(Task.elapsed_minutes), 0),
            )
            .select_from(Task)
            .join(Operation, Operation.id == Task.operation_id)
            .where(
                Task.status == TaskStatus.DONE,
                Task.finished_at >= since,
                Task.operator_id.is_not(None),
            )
            .group_by(Task.operator_id, Operation.kind)
        )
    ).all()

    found: dict[EntityId, _Row] = {}
    for operator_id, kind, completed, returns, norm, actual in rows:
        row = found.setdefault(operator_id, _Row(operator_id, (names or {}).get(operator_id, "")))
        row.completed += int(completed or 0)
        row.returns += int(returns or 0)
        row.norm += Decimal(str(norm or 0))
        row.actual += Decimal(str(actual or 0))
        row.by_kind[kind] = row.by_kind.get(kind, 0) + int(completed or 0)

    cards = [_card(row) for row in found.values()]
    return sorted(cards, key=lambda card: card.score or Decimal(0), reverse=True)


def _card(row: _Row) -> Scorecard:
    pace = pace_percent(row.norm, row.actual)
    return Scorecard(
        operator_id=row.operator_id,
        operator_name=row.name,
        completed=row.completed,
        returns=row.returns,
        pace_percent=pace,
        score=_score(row, pace),
        badges=_badges(row, pace),
    )


def _score(row: _Row, pace: Decimal | None) -> Decimal | None:
    """Pace × quality × volume, on a ten-point scale.

    Stated on the screen so it can be argued with. The three factors multiply
    rather than add on purpose: an operator who is fast and produces returns is
    not averagely good, and a sum would let volume buy its way past quality.
    """
    if pace is None or row.completed == 0:
        return None
    quality = Decimal(row.completed - row.returns) / Decimal(row.completed)
    # Volume saturates: past the tier ceiling more work does not make somebody
    # better, it just means they were on shift more.
    volume = min(Decimal(1), Decimal(row.completed) / Decimal(VOLUME_TIERS[0]))
    normalized = min(Decimal("1.2"), pace / 100)
    return (normalized * quality * volume * 10).quantize(Decimal("0.1"))


def _badges(row: _Row, pace: Decimal | None) -> list[Badge]:
    found = [
        Badge(
            code="badge.volume",
            tier=_tier(Decimal(row.completed), [Decimal(one) for one in VOLUME_TIERS]),
            detail={
                "completed": str(row.completed),
                "next": str(_next(row.completed, VOLUME_TIERS)),
            },
        ),
        Badge(
            code="badge.no_returns",
            # Three tiers by how much clean work is behind it. Zero returns over
            # two finished tasks is not an achievement, which is why the volume
            # is in the predicate rather than only the return count.
            tier=(
                _tier(Decimal(row.completed), [Decimal(one) for one in VOLUME_TIERS])
                if row.returns == 0
                else 0
            ),
            detail={"returns": str(row.returns), "completed": str(row.completed)},
        ),
        Badge(
            code="badge.pace",
            tier=_tier(pace, list(PACE_TIERS)) if pace is not None else 0,
            detail={"pace": str(pace) if pace is not None else ""},
        ),
    ]
    found.extend(
        Badge(
            code=f"badge.mastery.{kind.value}",
            tier=_tier(Decimal(count), [Decimal(one) for one in MASTERY_TIERS]),
            detail={"completed": str(count), "next": str(_next(count, MASTERY_TIERS))},
        )
        for kind, count in sorted(row.by_kind.items(), key=lambda item: item[1], reverse=True)
    )
    return found


def _tier(value: Decimal | None, thresholds: list[Decimal]) -> int:
    if value is None:
        return 0
    return sum(1 for threshold in thresholds if value >= threshold)


def _next(value: int, thresholds: tuple[int, ...]) -> int:
    """The next threshold, or the top one once it is passed.

    Returned so the client can say "31 из 50" — the distance to the next mark is
    what makes an unearned badge worth showing at all.
    """
    return next((one for one in thresholds if value < one), thresholds[-1])


__all__ = ["MASTERY_TIERS", "PACE_TIERS", "SCORE_DAYS", "VOLUME_TIERS", "scorecards"]
