"""The shared stock for everything that is not filament (#35, `inventory.articles`).

The rule under test is the one the two old per-context counts broke: **quantity is
never restated, only moved**, and every move is a ledger row in the same unit of
work. Pinned here:

* registering an item never changes its count;
* a receipt adds, records its reference, and moves the unit cost only when it has one;
* production's consumption clamps at zero and still records what was taken;
* a person's issue or write-off above the book is refused before anything changes;
* a correction from a count records by how much, and an agreeing count records nothing;
* placing honours a cell's declared capacity, counting spools and articles together;
* the store's map, cell panel and feed show the articles.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    MOVED_COUNTED,
    MOVED_ISSUED,
    MOVED_MOVED,
    MOVED_RECEIVED,
    MOVED_WRITTEN_OFF,
    ArticleKind,
    DryingPolicy,
    PlaceArticle,
    RegisterArticle,
    StockArticles,
    StoreViews,
    TakeArticle,
)
from printorian.contexts.inventory.article_models import ArticleMovement, StockArticle
from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.inventory.placement import PlacementService
from printorian.contexts.inventory.schemas import CreateStorageCell, CreateStorageZone
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.ids import new_id
from tests.factories import ensure_lot

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
NOZZLE = ArticleKind.SPARE_PART


async def ledger(db: AsyncSession, article_id: object) -> list[tuple[str, Decimal, Decimal]]:
    rows = await db.scalars(
        select(ArticleMovement)
        .where(ArticleMovement.article_id == article_id)
        .order_by(ArticleMovement.sequence)
    )
    return [(row.reason, row.quantity, row.on_hand_after) for row in rows]


async def a_nozzle(db: AsyncSession, *, on_hand: str = "10") -> StockArticle:
    return await StockArticles(db).receive(
        NOZZLE,
        "NZ-04",
        quantity=Decimal(on_hand),
        at=T0,
        name="Сопло 0.4",
        unit="piece",
        unit_cost=Decimal("450"),
        reference="PO-0090",
    )


async def test_registering_an_item_never_changes_its_count(db_session: AsyncSession) -> None:
    stock = StockArticles(db_session)
    article = await a_nozzle(db_session)

    view = await stock.register(
        RegisterArticle(kind=NOZZLE, code="NZ-04", name="Сопло латунь 0.4", reorder_at=Decimal(3))
    )

    assert view.on_hand == Decimal(10)
    assert view.name == "Сопло латунь 0.4"
    assert await ledger(db_session, article.id) == [(MOVED_RECEIVED, Decimal(10), Decimal(10))]


async def test_a_receipt_adds_with_its_reference_and_keeps_the_last_known_cost(
    db_session: AsyncSession,
) -> None:
    stock = StockArticles(db_session)
    article = await a_nozzle(db_session)

    # Counted at the door before the invoice: no price, so the last one stands.
    await stock.receive(NOZZLE, "NZ-04", quantity=Decimal(5), at=T0, reference="PO-0091")

    assert article.on_hand == Decimal(15)
    assert article.unit_cost == Decimal("450")
    movement = await db_session.scalar(select(ArticleMovement).where(ArticleMovement.sequence == 2))
    assert movement is not None and movement.reference == "PO-0091"


async def test_production_use_clamps_at_zero_and_still_records_what_was_taken(
    db_session: AsyncSession,
) -> None:
    """A packer who takes a box the book says is not there has still used it."""
    stock = StockArticles(db_session)
    article = await a_nozzle(db_session, on_hand="2")

    await stock.consume(NOZZLE, "NZ-04", Decimal(3), at=T0, reference="SV-0407")

    assert article.on_hand == Decimal(0)
    assert (await ledger(db_session, article.id))[-1] == (MOVED_ISSUED, Decimal(3), Decimal(0))


@pytest.mark.parametrize("take", ["issue", "write_off"])
async def test_a_person_taking_more_than_the_book_holds_is_refused_untouched(
    db_session: AsyncSession, take: str
) -> None:
    stock = StockArticles(db_session)
    article = await a_nozzle(db_session, on_hand="2")

    with pytest.raises(DomainRuleViolationError) as refused:
        await getattr(stock, take)(
            article.id, TakeArticle(quantity=Decimal(3)), at=T0, actor_id=None
        )

    assert refused.value.code == "error.inventory.take_exceeds_on_hand"
    assert article.on_hand == Decimal(2)
    assert len(await ledger(db_session, article.id)) == 1


async def test_issue_and_write_off_each_leave_their_reason(db_session: AsyncSession) -> None:
    stock = StockArticles(db_session)
    article = await a_nozzle(db_session)

    await stock.issue(
        article.id, TakeArticle(quantity=Decimal(1), reference="SV-0407"), at=T0, actor_id=None
    )
    after = await stock.write_off(
        article.id, TakeArticle(quantity=Decimal(2), note="сорвана резьба"), at=T0, actor_id=None
    )

    assert after.on_hand == Decimal(7)
    assert [reason for reason, _, _ in await ledger(db_session, article.id)] == [
        MOVED_RECEIVED,
        MOVED_ISSUED,
        MOVED_WRITTEN_OFF,
    ]


async def test_a_count_records_the_correction_and_an_agreeing_count_records_nothing(
    db_session: AsyncSession,
) -> None:
    stock = StockArticles(db_session)
    article = await a_nozzle(db_session)

    await stock.restate(NOZZLE, "NZ-04", Decimal(7), at=T0)
    await stock.restate(NOZZLE, "NZ-04", Decimal(7), at=T0 + timedelta(hours=1))

    assert await ledger(db_session, article.id) == [
        (MOVED_RECEIVED, Decimal(10), Decimal(10)),
        (MOVED_COUNTED, Decimal(3), Decimal(7)),
    ]


async def store(db: AsyncSession) -> None:
    placement = PlacementService(db)
    await placement.create_zone(CreateStorageZone(code="C", name="Запчасти"))
    await placement.create_cell(CreateStorageCell(zone_code="C", address="C5-1", capacity_lots=2))


async def test_placing_honours_capacity_counting_spools_and_articles_together(
    db_session: AsyncSession,
) -> None:
    await store(db_session)
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)
    lot = await db_session.get(MaterialLot, lot_id)
    assert lot is not None
    lot.remaining_grams = Decimal(500)
    await PlacementService(db_session).place_lot(lot_id, address="C5-1", at=T0)
    stock = StockArticles(db_session)
    nozzle = await a_nozzle(db_session)

    placed = await stock.place(nozzle.id, PlaceArticle(address="C5-1"), at=T0, actor_id=None)
    assert placed.cell == "C5-1"
    assert (await ledger(db_session, nozzle.id))[-1][0] == MOVED_MOVED

    belt = await stock.receive(NOZZLE, "BELT", quantity=Decimal(2), at=T0)
    with pytest.raises(DomainRuleViolationError) as full:
        await stock.place(belt.id, PlaceArticle(address="C5-1"), at=T0, actor_id=None)
    assert full.value.code == "error.inventory.cell_full"

    with pytest.raises(NotFoundError):
        await stock.place(belt.id, PlaceArticle(address="Z9-9"), at=T0, actor_id=None)


async def test_the_store_shows_articles_on_the_map_in_the_cell_and_in_the_feed(
    db_session: AsyncSession,
) -> None:
    await store(db_session)
    stock = StockArticles(db_session)
    nozzle = await a_nozzle(db_session)
    await stock.place(nozzle.id, PlaceArticle(address="C5-1"), at=T0, actor_id=None)
    views = StoreViews(db_session)

    [zone] = (await views.cell_map()).zones
    [cell] = zone.cells
    assert (cell.lot_count, cell.article_count) == (0, 1)
    # One of two declared places is taken: 50 %, counted from what is there.
    assert cell.fill_percent == Decimal(50)

    detail = await views.cell_detail("C5-1", now=T0, drying=DryingPolicy(required=False))
    assert [article.code for article in detail.articles] == ["NZ-04"]
    assert [row.reason for row in detail.article_movements] == [MOVED_MOVED]

    feed = await views.article_movements()
    assert [(row.code, row.reason) for row in feed] == [
        ("NZ-04", MOVED_MOVED),
        ("NZ-04", MOVED_RECEIVED),
    ]
