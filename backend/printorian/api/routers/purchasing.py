"""The purchasing desk over HTTP.

`MANAGE_INVENTORY` on the router — the permission `packaging.py`'s own docstring
describes as "those decide what the farm buys". Everything here is that decision
or a record of it.

**The money is a route split, not a blanked field.** `/board` and
`/orders/{po_id}` carry numbers, suppliers, composition, stages, dates and stock
consequences and no rubles at all; `/orders/{po_id}/costs` carries the prices and
needs `VIEW_FINANCIALS` on top. Nulling the money out of one response for a caller
without the permission would spell "not permitted" the way this system already
spells "not measured" (ADR-0007), and one spelling for two facts is how a screen
starts lying quietly. `GET /jobs/variances` refuses whole for the same reason, and
this follows it. `POST .../receive` carries a paid price in its *body* and
therefore needs both permissions too.

**The reorder list is composed here, not inside `procurement`.** The five
purchasable classes are stocked by four different contexts, and a read inside
procurement that reached into each would make it depend on all four. So this
module gathers — the farm's own thresholds from `settings`, the levels from
`inventory`, the queue's promises from `production` — and hands them to a pure
rule (ARCHITECTURE §layering).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, status

from printorian.api.deps import (
    AppClock,
    CurrentActor,
    DbSession,
    FarmSettings,
    Inventory,
    Procurement,
    requires,
)
from printorian.contexts.identity import Permission
from printorian.contexts.procurement import (
    AdvanceOrder,
    AssignSupplier,
    CreatePurchaseLine,
    CreatePurchaseOrder,
    CreateSupplier,
    PurchaseOrderCost,
    PurchaseOrderView,
    PurchasingBoard,
    ReceiveDelivery,
    ReorderRow,
    SupplierView,
    material_items,
    order_rows,
    ordered_codes,
    reorder_rows,
    seed_lines,
    status_counts,
)
from printorian.contexts.production import committed_material
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/purchasing",
    tags=["purchasing"],
    dependencies=[Depends(requires(Permission.MANAGE_INVENTORY))],
)

#: The extra gate on everything carrying a price. Declared once so the two routes
#: that need it cannot drift apart.
_MONEY = Depends(requires(Permission.VIEW_FINANCIALS))

#: The farm's own low-stock threshold. Shipped at 400 g since the settings
#: catalogue was built and, until this screen, read by nothing at all.
_LOW_STOCK_KEY = "inventory.low_stock_grams"
#: The farm's switch for the reorder panel. Off means the panel goes quiet rather
#: than nagging, which is what the setting has always claimed to do.
_AUTO_REORDER_KEY = "inventory.auto_reorder"


async def _reorder(
    db: DbSession, inventory: Inventory, farm: FarmSettings, *, on_order: frozenset[str]
) -> list[ReorderRow]:
    """«Требуют заказа сейчас», composed from the four things it needs.

    ``on_order`` is passed in rather than read here because the caller has already
    asked for it — the board also needs it for the materials levels, and asking
    twice would let the two halves of one screen disagree about the same instant.
    """
    low_at = Decimal(await farm.resolve_int(_LOW_STOCK_KEY))
    table = await inventory.table(on_order=on_order)
    # Grams and job counts the print queue has already promised away: the only
    # measured basis this system has for the «Последствие» column. Everything
    # else the design kit puts there is denominated in months of cover, and
    # nothing decrements `material_lots.remaining_grams` — see `ConsequenceKind`.
    committed = {
        row.material_code: (row.grams, row.job_count) for row in await committed_material(db)
    }
    return reorder_rows(
        material_items(table.rows, low_at=low_at),
        on_order=on_order,
        committed=committed,
        auto_reorder=await farm.resolve_bool(_AUTO_REORDER_KEY),
    )


@router.get("/board")
async def board(
    db: DbSession, clock: AppClock, inventory: Inventory, farm: FarmSettings
) -> PurchasingBoard:
    """The whole screen, read against one instant, and with no money in it.

    One response for the reason the packing board is one: the chips, the orders
    and the reorder list all describe the same moment, and a client fanning out
    would show an order in two chips at once.
    """
    on_order = await ordered_codes(db)
    orders = await order_rows(db)
    return PurchasingBoard(
        at=clock.now(),
        reorder=await _reorder(db, inventory, farm, on_order=on_order),
        orders=orders,
        counts=await status_counts(db),
        total=len(orders),
    )


@router.get("/suppliers")
async def suppliers(procurement: Procurement) -> list[SupplierView]:
    """Everybody the farm buys from, so «не выбран» can become somebody."""
    return await procurement.suppliers()


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
async def add_supplier(data: CreateSupplier, procurement: Procurement) -> SupplierView:
    return await procurement.add_supplier(data)


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def raise_order(
    data: CreatePurchaseOrder,
    db: DbSession,
    procurement: Procurement,
    inventory: Inventory,
    farm: FarmSettings,
) -> PurchaseOrderView:
    """Raise a draft, optionally seeded from the reorder list.

    ``seed_from_reorder`` is «Собрать один заказ». The lines it produces carry
    quantities and no prices: nobody has asked a supplier yet, and a guessed price
    would go straight into an order total as though somebody had quoted it.
    """
    seeded: list[CreatePurchaseLine] = []
    if data.seed_from_reorder:
        rows = await _reorder(db, inventory, farm, on_order=await ordered_codes(db))
        seeded = seed_lines(rows)
    return await procurement.raise_order(data, seeded=seeded)


@router.get("/orders/{po_id}")
async def order(po_id: EntityId, procurement: Procurement) -> PurchaseOrderView:
    """One purchase order in full. Unknown ids 404 rather than answering empty.

    **Declared after `/board` and `/suppliers` on purpose.** FastAPI matches in
    declaration order, and the `GET /jobs/variances` bug this repository already
    wrote up is what a fixed path below a `{param}` one looks like.
    """
    return await procurement.order(po_id)


@router.get("/orders/{po_id}/costs", dependencies=[_MONEY])
async def costs(po_id: EntityId, procurement: Procurement) -> PurchaseOrderCost:
    """«Стоимость заказа» — unit prices, line totals, «Заморозится в остатках».

    The only route in this module carrying rubles, and it is refused whole to a
    caller without `VIEW_FINANCIALS` rather than answering with the money nulled.
    See the module docstring.
    """
    return await procurement.costs(po_id)


@router.post("/orders/{po_id}/supplier")
async def assign_supplier(
    po_id: EntityId, data: AssignSupplier, procurement: Procurement
) -> PurchaseOrderView:
    """Say who this is being bought from — the kit's draft shows «не выбран»."""
    return await procurement.assign_supplier(po_id, data.supplier_id)


@router.post("/orders/{po_id}/lines")
async def add_lines(
    po_id: EntityId, lines: list[CreatePurchaseLine], procurement: Procurement
) -> PurchaseOrderView:
    return await procurement.add_lines(po_id, lines)


@router.post("/orders/{po_id}/status")
async def advance(
    po_id: EntityId, data: AdvanceOrder, procurement: Procurement
) -> PurchaseOrderView:
    """Move one stage along «Путь заказа», refusing an illegal jump by code."""
    return await procurement.advance(po_id, data.to)


@router.post("/orders/{po_id}/cancel")
async def cancel(po_id: EntityId, procurement: Procurement) -> PurchaseOrderView:
    """Drop an order. Its lines and receipts stay — they are what happened."""
    return await procurement.cancel(po_id)


@router.post("/orders/{po_id}/receive", dependencies=[_MONEY])
async def receive(
    po_id: EntityId, data: ReceiveDelivery, procurement: Procurement, actor: CurrentActor
) -> PurchaseOrderView:
    """Count a delivery in and put it on the shelf.

    `VIEW_FINANCIALS` as well as `MANAGE_INVENTORY`, because the body carries
    `unit_price_paid` — a request is as much a place money crosses the boundary as
    a response is, and the permission that guards reading a price has to guard
    writing one or the split is decorative.

    The actor is recorded on every receipt (`purchase_receipts.received_by`): who
    signed for a delivery is the first question asked when a count is wrong.
    """
    return await procurement.receive(po_id, data, by=actor.user_id)
