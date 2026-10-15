"""The stocktake and the two measures, for packaging, consumables and parts (#35).

The stocktake's rules carry over from spools unchanged, and each is pinned for
articles:

* **only items in a cell, with something on hand, are lined up** — one nobody has
  placed is nowhere a person could be sent to count it;
* **an uncounted line stays at its book value** — never zeroed;
* **close corrects against the book as it is at close**, not the snapshot, so an
  issue in between is not a shortage; the correction is a ledger row carrying the
  stocktake's number;
* **the money is costed only where a receipt recorded a cost**.

And the measures:

* **turnover is days of cover**, the mean over items used in the window, with the
  unused ones counted beside it;
* **dead stock** is what nothing has touched for the idle line, valued only where
  a cost was recorded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    MOVED_COUNTED,
    ArticleKind,
    PlaceArticle,
    StockArticles,
    StocktakeReads,
    StocktakeService,
    TakeArticle,
    article_histories,
    article_turnover,
    dead_articles,
)
from printorian.contexts.inventory.article_models import ArticleMovement, StockArticle
from printorian.contexts.inventory.placement import PlacementService
from printorian.contexts.inventory.schemas import CreateStorageCell, CreateStorageZone
from printorian.contexts.inventory.stocktake_reads import summary_of

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


async def shelves(db: AsyncSession) -> None:
    placement = PlacementService(db)
    await placement.create_zone(CreateStorageZone(code="C", name="Запчасти"))
    for address in ("C5-1", "C6-1"):
        await placement.create_cell(CreateStorageCell(zone_code="C", address=address))


async def stocked(
    db: AsyncSession,
    code: str,
    quantity: str,
    *,
    at: datetime = T0,
    cell: str | None = None,
    cost: str | None = None,
    kind: ArticleKind = ArticleKind.PACKAGING,
) -> StockArticle:
    stock = StockArticles(db)
    article = await stock.receive(
        kind,
        code,
        quantity=Decimal(quantity),
        at=at,
        unit_cost=Decimal(cost) if cost else None,
    )
    if cell is not None:
        await stock.place(article.id, PlaceArticle(address=cell), at=at, actor_id=None)
    return article


async def test_a_count_lines_up_only_placed_items_with_stock_and_corrects_against_the_book_now(
    db_session: AsyncSession,
) -> None:
    await shelves(db_session)
    box = await stocked(db_session, "box-a", "40", cell="C6-1", cost="62")
    film = await stocked(db_session, "film", "3", cell="C6-1")
    await stocked(db_session, "unplaced", "9")
    service = StocktakeService(db_session)

    stocktake = await service.open(zone_code=None, at=T0 + timedelta(days=1))
    assert sorted(line.code for line in stocktake.article_lines) == ["box-a", "film"]

    await service.count_article(
        stocktake.id, box.id, quantity=Decimal(30), at=T0 + timedelta(days=1)
    )
    # Six go out to a parcel after the line was snapshotted at forty.
    await StockArticles(db_session).issue(
        box.id, TakeArticle(quantity=Decimal(6)), at=T0 + timedelta(days=1), actor_id=None
    )
    closed = await service.close(stocktake.id, at=T0 + timedelta(days=2))

    lines = {line.code: line for line in closed.article_lines}
    # 30 found against 34 on the book at close: four short, not ten.
    assert lines["box-a"].variance == Decimal(-4)
    assert box.on_hand == Decimal(30)
    # Never counted: stays at its book value, and says it was not checked.
    assert lines["film"].counted is None and lines["film"].variance is None
    assert film.on_hand == Decimal(3)

    last = await db_session.scalar(
        select(ArticleMovement)
        .where(ArticleMovement.article_id == box.id)
        .order_by(ArticleMovement.sequence.desc())
    )
    assert last is not None
    assert (last.reason, last.quantity, last.note) == (MOVED_COUNTED, Decimal(4), closed.number)

    summary = summary_of(closed)
    assert (summary.positions, summary.counted, summary.short) == (2, 1, 1)


async def test_the_shortage_is_costed_only_where_a_cost_was_recorded(
    db_session: AsyncSession,
) -> None:
    await shelves(db_session)
    priced = await stocked(db_session, "box-a", "10", cell="C6-1", cost="62")
    unpriced = await stocked(db_session, "film", "5", cell="C6-1")
    service = StocktakeService(db_session)
    stocktake = await service.open(zone_code=None, at=T0)
    await service.count_article(stocktake.id, priced.id, quantity=Decimal(8), at=T0)
    await service.count_article(stocktake.id, unpriced.id, quantity=Decimal(4), at=T0)
    await service.close(stocktake.id, at=T0)

    value = await StocktakeReads(db_session).value(stocktake.id)

    assert value.short_value == Decimal("124.00")
    assert value.unpriced_lines == 1


async def test_turnover_is_days_of_cover_with_the_unused_counted_beside(
    db_session: AsyncSession,
) -> None:
    now = T0 + timedelta(days=30)
    stock = StockArticles(db_session)
    used = await stocked(db_session, "box-a", "60")
    # Thirty used over a thirty-day window: one a day, so thirty left is 30 days.
    await stock.issue(used.id, TakeArticle(quantity=Decimal(30)), at=T0, actor_id=None)
    await stocked(db_session, "box-b", "20")

    rows = article_turnover(
        await article_histories(db_session, since=now - timedelta(days=30)), window_days=30
    )

    [packaging] = rows
    assert packaging.kind is ArticleKind.PACKAGING
    assert (packaging.measured, packaging.mean_days_of_cover, packaging.idle) == (
        1,
        Decimal("30.0"),
        1,
    )


async def test_dead_stock_is_what_nothing_touched_valued_where_a_cost_exists(
    db_session: AsyncSession,
) -> None:
    await stocked(db_session, "resin", "2", cost="2700", kind=ArticleKind.POST_CONSUMABLE)
    await stocked(db_session, "pei", "1", kind=ArticleKind.SPARE_PART)
    await stocked(db_session, "fresh", "5", at=T0 + timedelta(days=100))

    rows, value, unpriced = dead_articles(
        await article_histories(db_session, since=T0), now=T0 + timedelta(days=122), idle_days=90
    )

    assert sorted(row.code for row in rows) == ["pei", "resin"]
    assert value == Decimal("5400.00")
    assert unpriced == 1
