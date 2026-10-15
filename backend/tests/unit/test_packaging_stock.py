"""Packing and buying, through the shared stock (#35).

Two paths that used to keep their own counts now move the same article:

* **a packer choosing a box consumes it, and changing their mind gives the first
  one back** — the old code deleted its usage rows and decremented again, so a
  corrected parcel took two boxes off the shelf for one;
* **a received carton of packaging reaches the shelf**, with the purchase order as
  its reason, and the tara table reads the new count. Before #35 the receipt was
  refused outright — packaging had no stock it could be added to.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    MOVED_ISSUED,
    MOVED_RECEIVED,
    MOVED_RETURNED,
    ArticleKind,
    InventoryService,
    StockArticles,
)
from printorian.contexts.inventory.article_models import ArticleMovement, StockArticle
from printorian.contexts.packaging import ChooseTara, enclosures, tara_rows
from printorian.contexts.procurement import (
    CreatePurchaseLine,
    CreatePurchaseOrder,
    ProcurementService,
    PurchasableKind,
    PurchaseStatus,
    ReceiveDelivery,
    ReceiveLine,
)
from printorian.contexts.procurement.models import PurchaseReceipt
from printorian.core.clock import FixedClock
from tests.unit._packaging_support import a_packer, a_parcel, a_shelf


async def box_ledger(db: AsyncSession, code: str) -> list[tuple[str, Decimal, Decimal, str | None]]:
    rows = await db.execute(
        select(ArticleMovement)
        .join(StockArticle, StockArticle.id == ArticleMovement.article_id)
        .where(StockArticle.kind == ArticleKind.PACKAGING, StockArticle.code == code)
        .order_by(ArticleMovement.sequence)
    )
    return [(row.reason, row.quantity, row.on_hand_after, row.reference) for row in rows.scalars()]


async def test_changing_the_box_puts_the_first_one_back(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    service, parcel = await a_parcel(db_session, clock)
    await service.start(parcel.id, await a_packer(db_session))
    shelf = {tara.code: tara for tara in await enclosures(db_session)}

    await service.choose_tara(parcel.id, ChooseTara(tara_id=shelf["box-a"].id, extras={}))
    await service.choose_tara(parcel.id, ChooseTara(tara_id=shelf["box-b"].id, extras={}))

    counts = await StockArticles(db_session).on_hand(ArticleKind.PACKAGING, ["box-a", "box-b"])
    # Ten of each were stocked; one parcel is in box B, and box A is back to ten.
    assert counts == {"box-a": Decimal(10), "box-b": Decimal(9)}
    taken_and_returned = (await box_ledger(db_session, "box-a"))[1:]
    assert taken_and_returned == [
        (MOVED_ISSUED, Decimal(1), Decimal(9), parcel.number),
        (MOVED_RETURNED, Decimal(1), Decimal(10), parcel.number),
    ]
    # The catalogue's own column follows the article while both exist.
    assert (shelf["box-a"].stock, shelf["box-b"].stock) == (Decimal(10), Decimal(9))


async def test_a_received_carton_of_packaging_reaches_the_shelf_and_the_tara_table(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    await a_shelf(db_session)
    procurement = ProcurementService(db_session, clock, lots=InventoryService(db_session))
    order = await procurement.raise_order(
        CreatePurchaseOrder(
            lines=[
                CreatePurchaseLine(
                    kind=PurchasableKind.PACKAGING,
                    item_code="box-a",
                    item_name="Коробка A",
                    quantity=Decimal(50),
                    unit="piece",
                )
            ]
        )
    )
    await procurement.advance(order.id, PurchaseStatus.APPROVED)
    await procurement.advance(order.id, PurchaseStatus.PAID)

    await procurement.receive(
        order.id,
        ReceiveDelivery(
            lines=[
                ReceiveLine(
                    line_id=order.lines[0].id,
                    quantity=Decimal(50),
                    unit_price_paid=Decimal("58.00"),
                )
            ]
        ),
        by=None,
    )

    receipt = await db_session.scalar(select(PurchaseReceipt))
    article = await db_session.scalar(select(StockArticle).where(StockArticle.code == "box-a"))
    assert receipt is not None and article is not None
    assert receipt.stock_article_id == article.id
    assert article.on_hand == Decimal(60)
    assert article.unit_cost == Decimal("58.00")
    assert (await box_ledger(db_session, "box-a"))[-1] == (
        MOVED_RECEIVED,
        Decimal(50),
        Decimal(60),
        order.number,
    )
    table = {row.code: row for row in await tara_rows(db_session, now=clock.now())}
    assert table["box-a"].stock == Decimal(60)


async def test_a_spare_part_the_farm_never_stocked_is_declared_by_its_delivery(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    procurement = ProcurementService(db_session, clock, lots=InventoryService(db_session))
    order = await procurement.raise_order(
        CreatePurchaseOrder(
            lines=[
                CreatePurchaseLine(
                    kind=PurchasableKind.SPARE_PART,
                    item_code="NZ-04",
                    item_name="Сопло 0.4",
                    quantity=Decimal(6),
                    unit="piece",
                )
            ]
        )
    )
    await procurement.advance(order.id, PurchaseStatus.APPROVED)
    await procurement.advance(order.id, PurchaseStatus.PAID)

    await procurement.receive(
        order.id,
        ReceiveDelivery(lines=[ReceiveLine(line_id=order.lines[0].id, quantity=Decimal(6))]),
        by=None,
    )

    [part] = await StockArticles(db_session).rows(ArticleKind.SPARE_PART)
    assert (part.code, part.name, part.unit, part.on_hand) == (
        "NZ-04",
        "Сопло 0.4",
        "piece",
        Decimal(6),
    )
