"""«Оборачиваемость» and «Залежалое» for the non-filament stock (#35).

`store_measures` answers both for spools, from the spool ledger. This answers
them for articles, from theirs — and the two cannot share one formula, because
the two kinds of stock are not the same kind of thing.

**Turnover is days of cover, not days per lot.** A spool arrives as one object
and leaves as one, so "how long did it sit" has an answer per lot. A box of film
does not: forty boxes arrive, six go on Monday, another thirty arrive on
Wednesday, and no box has a history of its own. What the ledger *can* say is how
fast an item is used — the quantity issued or written off over the window — and
how long what is on hand would last at that rate. That is the honest figure for
fungible stock, and it answers the kit's question («сколько дней лежит на полке»)
in the only way the records allow. Per kind, it is the mean over the items that
were used in the window; items with stock and no use are counted beside it
(`idle`), never inside — an untouched shelf would otherwise read as a fast one,
or divide by zero (CLAUDE.md §1).

**Dead stock is money, kept apart as it is for spools.** An article on hand that
no movement has touched for ``idle_days`` is costed at `unit_cost × on_hand` only
where a receipt recorded a cost; an article with none is counted
(`unpriced_articles`) and adds nothing. Served on the same `VIEW_FINANCIALS` route
as the spools' half.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory.article_models import ArticleMovement, StockArticle
from printorian.contexts.inventory.movements import MOVED_ISSUED, MOVED_WRITTEN_OFF
from printorian.contexts.inventory.policies import ArticleKind
from printorian.core.ids import EntityId

#: What takes an article off the shelf for good. A move is cell to cell; a
#: return puts some back; a count corrects the book rather than using anything.
USED: frozenset[str] = frozenset({MOVED_ISSUED, MOVED_WRITTEN_OFF})

_DAY_PLACES = Decimal("0.1")
_MONEY_PLACES = Decimal("0.01")
_SECONDS_PER_DAY = Decimal(86400)


@dataclass(frozen=True, slots=True)
class ArticleHistory:
    article_id: EntityId
    kind: ArticleKind
    code: str
    name: str
    unit: str
    on_hand: Decimal
    unit_cost: Decimal | None
    #: How much left the shelf in the window, in the article's unit.
    used_in_window: Decimal
    #: The newest movement of any reason, or ``None`` for an item never moved.
    last_moved_at: datetime | None


class ArticleTurnoverRow(BaseModel):
    kind: ArticleKind
    #: Items used in the window — the denominator of the mean.
    measured: int = 0
    #: Mean days the stock on hand would last at the window's rate. ``None`` with none.
    mean_days_of_cover: Decimal | None = None
    #: Items with stock that nothing used in the window: counted beside, never inside.
    idle: int = 0


class DeadStockArticle(BaseModel):
    article_id: EntityId
    kind: ArticleKind
    code: str
    name: str
    unit: str
    on_hand: Decimal
    idle_days: Decimal
    #: `unit_cost × on_hand`, or ``None`` where no receipt recorded a cost.
    value: Decimal | None = None


def _days(start: datetime, end: datetime) -> Decimal:
    return Decimal(int((end - start).total_seconds())) / _SECONDS_PER_DAY


def article_turnover(
    items: Sequence[ArticleHistory], *, window_days: int
) -> list[ArticleTurnoverRow]:
    """Per kind: how long what is on hand would last at the window's rate. Pure."""
    cover: dict[ArticleKind, list[Decimal]] = {}
    idle: dict[ArticleKind, int] = {}
    for item in items:
        if item.used_in_window <= 0:
            if item.on_hand > 0:
                idle[item.kind] = idle.get(item.kind, 0) + 1
            continue
        per_day = item.used_in_window / Decimal(window_days)
        cover.setdefault(item.kind, []).append(item.on_hand / per_day)
    rows: list[ArticleTurnoverRow] = []
    for kind in sorted(set(cover) | set(idle), key=lambda k: k.value):
        days = cover.get(kind, [])
        rows.append(
            ArticleTurnoverRow(
                kind=kind,
                measured=len(days),
                mean_days_of_cover=(
                    (sum(days, Decimal(0)) / Decimal(len(days))).quantize(_DAY_PLACES)
                    if days
                    else None
                ),
                idle=idle.get(kind, 0),
            )
        )
    return rows


def dead_articles(
    items: Sequence[ArticleHistory], *, now: datetime, idle_days: int
) -> tuple[list[DeadStockArticle], Decimal, int]:
    """Articles on hand that nothing has touched for ``idle_days``, with their value.

    Returns the rows, the value of the priced ones, and how many it could not
    price. Pure.
    """
    rows: list[DeadStockArticle] = []
    value_total = Decimal(0)
    unpriced = 0
    for item in items:
        if item.on_hand <= 0 or item.last_moved_at is None:
            continue
        idle = _days(item.last_moved_at, now)
        if idle < Decimal(idle_days):
            continue
        value: Decimal | None = None
        if item.unit_cost is not None:
            value = (item.unit_cost * item.on_hand).quantize(_MONEY_PLACES)
            value_total += value
        else:
            unpriced += 1
        rows.append(
            DeadStockArticle(
                article_id=item.article_id,
                kind=item.kind,
                code=item.code,
                name=item.name,
                unit=item.unit,
                on_hand=item.on_hand,
                idle_days=idle.quantize(_DAY_PLACES),
                value=value,
            )
        )
    rows.sort(key=lambda row: row.idle_days, reverse=True)
    return rows, value_total, unpriced


async def article_histories(db: AsyncSession, *, since: datetime) -> list[ArticleHistory]:
    """Every active article, with what it used since ``since`` and when it last moved."""
    used = (
        select(
            ArticleMovement.article_id,
            func.coalesce(
                func.sum(
                    case(
                        (
                            (ArticleMovement.reason.in_(USED)) & (ArticleMovement.at >= since),
                            ArticleMovement.quantity,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("used"),
            func.max(ArticleMovement.at).label("last"),
        )
        .group_by(ArticleMovement.article_id)
        .subquery()
    )
    rows = await db.execute(
        select(StockArticle, used.c.used, used.c.last)
        .outerjoin(used, used.c.article_id == StockArticle.id)
        .where(StockArticle.is_active.is_(True))
    )
    return [
        ArticleHistory(
            article_id=article.id,
            kind=article.kind,
            code=article.code,
            name=article.name,
            unit=article.unit,
            on_hand=article.on_hand,
            unit_cost=article.unit_cost,
            used_in_window=Decimal(used_quantity or 0),
            last_moved_at=last,
        )
        for article, used_quantity, last in rows.all()
    ]


__all__ = [
    "USED",
    "ArticleHistory",
    "ArticleTurnoverRow",
    "DeadStockArticle",
    "article_histories",
    "article_turnover",
    "dead_articles",
]
