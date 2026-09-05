"""The arrival: counting a delivery in and putting it on the shelf.

The near-irreversible path on the purchasing screen, so it gets the most care. A
receipt that created a spool nobody ordered, or counted a box twice, is not undone
by editing a row — the shelf now disagrees with the record, and the next thing to
notice is a scheduler that thinks it has filament.

Two of these read the `MaterialLot` back rather than trusting the response.
`material_lots.purchase_price` and `.lot_number` were declared when the table was
built and written by nothing at all until receiving existed; a test that only
asserted on the returned view would pass against a service that dropped both on
the floor.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import CreateMaterialSpec, InventoryService
from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
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
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import EntityId

MATERIAL = "PLA-BLACK"


@pytest.fixture
def procurement(db_session: AsyncSession, clock: FixedClock) -> ProcurementService:
    """The service as `api/deps.py` builds it — with the inventory writer it
    needs, and nothing deeper than that context's public surface."""
    return ProcurementService(db_session, clock, lots=InventoryService(db_session))


@pytest.fixture(autouse=True)
async def _the_catalogue_knows_the_filament(db_session: AsyncSession) -> None:
    """A received line becomes a lot *of* something, and `material_lots.spec_id`
    is a real foreign key."""
    await InventoryService(db_session).create_spec(
        CreateMaterialSpec(code=MATERIAL, name="PLA Black", family="PLA")
    )


async def an_order(
    procurement: ProcurementService,
    *,
    kind: PurchasableKind = PurchasableKind.MATERIAL,
    item_code: str = MATERIAL,
    quantity: str = "1000",
    at: PurchaseStatus = PurchaseStatus.PAID,
) -> tuple[EntityId, EntityId]:
    """One order walked to ``at``, as ``(order_id, line_id)``."""
    order = await procurement.raise_order(
        CreatePurchaseOrder(
            lines=[
                CreatePurchaseLine(
                    kind=kind, item_code=item_code, quantity=Decimal(quantity), unit="gram"
                )
            ]
        )
    )
    if at is not PurchaseStatus.DRAFT:
        order = await procurement.advance(order.id, PurchaseStatus.APPROVED)
    if at is PurchaseStatus.PAID:
        order = await procurement.advance(order.id, PurchaseStatus.PAID)
    return order.id, order.lines[0].id


async def test_receiving_a_material_line_creates_a_lot_carrying_its_number_and_paid_price(
    procurement: ProcurementService, db_session: AsyncSession
) -> None:
    """The two columns nothing wrote before, read back off the row itself."""
    order_id, line_id = await an_order(procurement)

    view = await procurement.receive(
        order_id,
        ReceiveDelivery(
            lines=[
                ReceiveLine(
                    line_id=line_id,
                    quantity=Decimal(1000),
                    unit_price_paid=Decimal("1.80"),
                    lot_number="B-4471",
                    shelf="A-3",
                )
            ]
        ),
        by=None,
    )

    lot = await db_session.scalar(select(MaterialLot))
    assert lot is not None
    assert lot.lot_number == "B-4471"
    # A lot total, not a rate: 1000 g at 1.80 ₽/g sits beside `initial_grams` on
    # one physical spool.
    assert lot.purchase_price == Decimal("1800.00")
    assert lot.remaining_grams == Decimal(1000)
    assert lot.shelf == "A-3"
    assert view.lines[0].received_quantity == Decimal(1000)


async def test_a_delivery_with_no_invoice_yet_records_no_price_rather_than_zero(
    procurement: ProcurementService, db_session: AsyncSession
) -> None:
    """Counted at the door before the paperwork caught up is a real thing, and it
    is not the same as free — a zero would drag every later average down with a
    purchase that never happened at that price."""
    order_id, line_id = await an_order(procurement)

    await procurement.receive(
        order_id,
        ReceiveDelivery(lines=[ReceiveLine(line_id=line_id, quantity=Decimal(1000))]),
        by=None,
    )

    lot = await db_session.scalar(select(MaterialLot))
    assert lot is not None and lot.purchase_price is None
    receipt = await db_session.scalar(select(PurchaseReceipt))
    assert receipt is not None and receipt.unit_price_paid is None


async def test_a_partial_receipt_leaves_the_order_open_and_records_only_what_arrived(
    procurement: ProcurementService,
) -> None:
    """Half a delivery moves the order to «Приёмка» and no further. `STORED` is a
    person saying the shelf is straight, which is why the two are different
    stages at all."""
    order_id, line_id = await an_order(procurement)

    view = await procurement.receive(
        order_id,
        ReceiveDelivery(lines=[ReceiveLine(line_id=line_id, quantity=Decimal(400))]),
        by=None,
    )

    assert view.status is PurchaseStatus.RECEIVING
    assert view.lines[0].received_quantity == Decimal(400)
    assert view.lines[0].quantity == Decimal(1000)


async def test_a_second_receipt_cannot_take_the_line_past_what_was_ordered(
    procurement: ProcurementService, db_session: AsyncSession
) -> None:
    """The case a per-request check against the ordered quantity alone would miss,
    and the one that actually happens: two half deliveries that together overrun."""
    order_id, line_id = await an_order(procurement)
    delivery = ReceiveDelivery(lines=[ReceiveLine(line_id=line_id, quantity=Decimal(600))])
    await procurement.receive(order_id, delivery, by=None)

    with pytest.raises(ValidationError) as raised:
        await procurement.receive(order_id, delivery, by=None)

    assert raised.value.code == "error.procurement.over_receipt"
    assert Decimal(raised.value.details["ordered"]) == Decimal(1000)
    assert Decimal(raised.value.details["already_received"]) == Decimal(600)
    # And the refusal wrote nothing: one receipt, not two.
    receipts = list(await db_session.scalars(select(PurchaseReceipt)))
    assert len(receipts) == 1


async def test_two_entries_in_one_delivery_cannot_together_overrun_the_line(
    procurement: ProcurementService,
) -> None:
    """Each entry is checked against what the *line* has taken so far, including
    the entries earlier in this same request. Checking against the database as it
    stood when the request began would let both through."""
    order_id, line_id = await an_order(procurement)

    with pytest.raises(ValidationError) as raised:
        await procurement.receive(
            order_id,
            ReceiveDelivery(
                lines=[
                    ReceiveLine(line_id=line_id, quantity=Decimal(600)),
                    ReceiveLine(line_id=line_id, quantity=Decimal(600)),
                ]
            ),
            by=None,
        )

    assert raised.value.code == "error.procurement.over_receipt"


async def test_receiving_a_class_with_no_stock_model_is_refused_by_code(
    procurement: ProcurementService, db_session: AsyncSession
) -> None:
    """An honest refusal rather than a silent no-op.

    Packaging and post-production both carry stock but neither offers an
    *increment*, and spare parts have no stock model at all (issue #33 claims that
    table). A receipt that recorded the arrival and put nothing on a shelf would
    leave a buyer believing a box of film had been stored.
    """
    order_id, line_id = await an_order(
        procurement, kind=PurchasableKind.PACKAGING, item_code="BOX-M", quantity="20"
    )

    with pytest.raises(ValidationError) as raised:
        await procurement.receive(
            order_id,
            ReceiveDelivery(lines=[ReceiveLine(line_id=line_id, quantity=Decimal(20))]),
            by=None,
        )

    assert raised.value.code == "error.procurement.class_not_receivable"
    assert raised.value.details["kind"] == "packaging"
    assert raised.value.details["receivable"] == ["material"]
    assert list(await db_session.scalars(select(PurchaseReceipt))) == []


async def test_nothing_arrives_against_an_order_still_in_draft(
    procurement: ProcurementService,
) -> None:
    """A draft is a thought. Goods turning up against one means somebody is on the
    wrong record, which is worth saying rather than absorbing."""
    order_id, line_id = await an_order(procurement, at=PurchaseStatus.DRAFT)

    with pytest.raises(ValidationError) as raised:
        await procurement.receive(
            order_id,
            ReceiveDelivery(lines=[ReceiveLine(line_id=line_id, quantity=Decimal(10))]),
            by=None,
        )

    assert raised.value.code == "error.procurement.order_not_arriving"
    assert raised.value.details["status"] == "draft"


async def test_a_line_from_another_order_is_refused_rather_than_ignored(
    procurement: ProcurementService,
) -> None:
    """A delivery half of which silently did nothing is worse than one refused
    whole: the half that vanished is the half nobody goes looking for."""
    order_id, _ = await an_order(procurement)
    _, other_line = await an_order(procurement)

    with pytest.raises(NotFoundError) as raised:
        await procurement.receive(
            order_id,
            ReceiveDelivery(lines=[ReceiveLine(line_id=other_line, quantity=Decimal(10))]),
            by=None,
        )

    assert raised.value.code == "error.procurement.line_not_found"


async def test_receiving_does_not_move_the_catalogue_price(
    procurement: ProcurementService, db_session: AsyncSession
) -> None:
    """ADR-0020, and the thing this slice most invites somebody to "finish".

    `design/purchasing.html` says twice that the purchase price reaches the tariff
    after receiving. `MaterialSpec.purchase_price_per_1000m` is a catalogue rate
    the pricing engine's numbers are derived from, and a receipt moving it would
    be a rate edit with no settings-audit row and no snapshot behind it — under a
    guarantee that changing a rate never reprices work already quoted. Receiving
    records the measurement and stops.
    """
    order_id, line_id = await an_order(procurement)

    await procurement.receive(
        order_id,
        ReceiveDelivery(
            lines=[
                ReceiveLine(
                    line_id=line_id, quantity=Decimal(1000), unit_price_paid=Decimal("1.80")
                )
            ]
        ),
        by=None,
    )

    spec = await db_session.scalar(select(MaterialSpec).where(MaterialSpec.code == MATERIAL))
    assert spec is not None and spec.purchase_price_per_1000m is None
