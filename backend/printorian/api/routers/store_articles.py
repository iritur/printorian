"""The non-filament half of the store: packaging, consumables, spare parts (#35).

Its own router for the reason `store_stocktakes.py` gave: `store.py` is at its
seam, and this is a different thing on the same shelves — not where a spool is,
but how many boxes, cans and nozzles there are and where they sit.

The permission shape is the store's. Reads take the router's `VIEW_PRODUCTION`;
every write takes `MANAGE_INVENTORY`, because each one changes the book. No money
on any of these routes: an article's purchase cost stays in the database, and it
reaches a screen only through the dead-stock report behind `VIEW_FINANCIALS`.

``/movements`` is declared before ``/{article_id}`` routes for the reason the
held-payments route is (`payments.py`): a path parameter typed as an id would
otherwise take ``movements`` and answer 422.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from printorian.api.deps import AppClock, CurrentActor, DbSession, requires
from printorian.contexts.identity import Permission
from printorian.contexts.inventory import (
    ArticleKind,
    ArticleMovementView,
    ArticleView,
    PlaceArticle,
    RegisterArticle,
    StockArticles,
    StoreViews,
    TakeArticle,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/store/articles",
    tags=["store"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

_MANAGES = Depends(requires(Permission.MANAGE_INVENTORY))


@router.get("")
async def articles(db: DbSession, kind: ArticleKind | None = None) -> list[ArticleView]:
    """Every stocked item that is not filament, by kind and code."""
    return await StockArticles(db).rows(kind)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGES])
async def register_article(data: RegisterArticle, db: DbSession) -> ArticleView:
    """Declare an item, or rename it and move its reorder line. Never its count."""
    return await StockArticles(db).register(data)


@router.get("/movements")
async def article_movements(
    db: DbSession,
    since: datetime | None = None,
    address: str | None = None,
    article_id: EntityId | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> list[ArticleMovementView]:
    """The non-filament half of «Движения», newest first.

    Apart from `/store/movements` because the two ledgers carry different units;
    the screen interleaves them by time.
    """
    return await StoreViews(db).article_movements(
        since=since, address=address, article_id=article_id, limit=limit
    )


@router.post("/{article_id}/place", dependencies=[_MANAGES])
async def place_article(
    article_id: EntityId, data: PlaceArticle, db: DbSession, actor: CurrentActor, clock: AppClock
) -> ArticleView:
    """Put an item in a cell, or move it between two, appending a movement."""
    return await StockArticles(db).place(article_id, data, at=clock.now(), actor_id=actor.user_id)


@router.post("/{article_id}/issue", dependencies=[_MANAGES])
async def issue_article(
    article_id: EntityId, data: TakeArticle, db: DbSession, actor: CurrentActor, clock: AppClock
) -> ArticleView:
    """Hand some to the floor — a nozzle to a printer, a can to the post."""
    return await StockArticles(db).issue(article_id, data, at=clock.now(), actor_id=actor.user_id)


@router.post("/{article_id}/write-off", dependencies=[_MANAGES])
async def write_off_article(
    article_id: EntityId, data: TakeArticle, db: DbSession, actor: CurrentActor, clock: AppClock
) -> ArticleView:
    """Gone for good. Refused, before anything changes, above what the book holds."""
    return await StockArticles(db).write_off(
        article_id, data, at=clock.now(), actor_id=actor.user_id
    )
