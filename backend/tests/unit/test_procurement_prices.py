"""«Цены по ключевым позициям» — picked from receipts, never typed in.

Issue #34's last row. The panel is a read over `purchase_receipts`, and the two
rules that make it an honest one are about the denominator:

* a receipt with no recorded price is a delivery and not a price of zero — it is
  counted, and it moves no figure;
* «было» is an earlier *priced* receipt in the window, or nothing — one point is
  a price, not a stable price.

The fold is pure and is tested first; the read is then driven once through the
real receiving path, so the window and the join are proved against rows the
system itself wrote.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import CreateMaterialSpec, InventoryService
from printorian.contexts.procurement import (
    CreatePurchaseLine,
    CreatePurchaseOrder,
    ProcurementService,
    PurchasableKind,
    PurchaseStatus,
    ReceiptRow,
    ReceiveDelivery,
    ReceiveLine,
    fold_prices,
    price_movements,
)
from printorian.core.clock import FixedClock

T0 = datetime.fromisoformat("2026-01-10T09:00:00+00:00")


def a_receipt(
    price: str | None,
    *,
    days: int,
    code: str = "PLA-BLACK",
    kind: PurchasableKind = PurchasableKind.MATERIAL,
    name: str = "PLA Black",
) -> ReceiptRow:
    return ReceiptRow(
        kind=kind,
        item_code=code,
        item_name=name,
        unit="gram",
        received_at=T0 + timedelta(days=days),
        unit_price_paid=None if price is None else Decimal(price),
    )


# ------------------------------------------------------------------ the fold


def test_earliest_and_latest_come_from_priced_receipts_and_the_unpriced_are_only_counted() -> None:
    """Four arrivals: 1.80, then one with no invoice yet, then 1.62, then 1.50.

    «Было» is 1.80 and the latest 1.50 whatever the middle one says, because it
    says nothing. It is in `unpriced_receipts` and in no figure.
    """
    rows = [
        a_receipt("1.80", days=0),
        a_receipt(None, days=30),
        a_receipt("1.62", days=60),
        a_receipt("1.50", days=90),
    ]

    [position] = fold_prices(rows)

    assert position.latest == Decimal("1.50")
    assert position.latest_at == T0 + timedelta(days=90)
    assert position.earliest == Decimal("1.80")
    assert position.earliest_at == T0
    assert position.change == Decimal("-0.1667")
    assert position.priced_receipts == 3
    assert position.unpriced_receipts == 1


def test_one_priced_receipt_is_a_price_and_not_a_movement() -> None:
    """Null for «было» and for the change — not `0`, which would claim the
    price held steady over a period nobody has a second point for."""
    [position] = fold_prices([a_receipt(None, days=0), a_receipt("2.10", days=5)])

    assert position.latest == Decimal("2.10")
    assert position.earliest is None
    assert position.earliest_at is None
    assert position.change is None
    assert position.priced_receipts == 1
    assert position.unpriced_receipts == 1


def test_a_position_with_only_unpriced_receipts_is_not_on_the_panel() -> None:
    """Deliveries, but nothing to draw: the row would have to invent its figure."""
    assert fold_prices([a_receipt(None, days=0), a_receipt(None, days=1)]) == []


def test_the_fold_sorts_by_time_itself_rather_than_trusting_the_query() -> None:
    """Rows arrive newest-first here, and «было» is still the older one."""
    [position] = fold_prices([a_receipt("1.50", days=90), a_receipt("1.80", days=0)])

    assert position.earliest == Decimal("1.80")
    assert position.latest == Decimal("1.50")


def test_positions_are_grouped_by_class_and_code_and_named_by_the_latest_line() -> None:
    """Two classes sharing a code are two positions; a renamed line reads as it is
    called now."""
    rows = [
        a_receipt("1.80", days=0, name="PLA (old name)"),
        a_receipt("1.70", days=10, name="PLA Black"),
        a_receipt("940", days=3, code="NOZZLE-HARD", kind=PurchasableKind.SPARE_PART, name="Сопло"),
        a_receipt("9.00", days=1, code="PLA-BLACK", kind=PurchasableKind.PACKAGING, name="Коробка"),
    ]

    positions = fold_prices(rows)

    assert [(p.kind, p.item_code, p.item_name) for p in positions] == [
        (PurchasableKind.MATERIAL, "PLA-BLACK", "PLA Black"),
        (PurchasableKind.PACKAGING, "PLA-BLACK", "Коробка"),
        (PurchasableKind.SPARE_PART, "NOZZLE-HARD", "Сопло"),
    ]


def test_a_dearer_latest_is_a_positive_change() -> None:
    [position] = fold_prices([a_receipt("980", days=0), a_receipt("1200", days=200)])

    assert position.change == Decimal("0.2245")


# ------------------------------------------------------------------ the read

MATERIAL = "PLA-BLACK"


@pytest.fixture
def procurement(db_session: AsyncSession, clock: FixedClock) -> ProcurementService:
    return ProcurementService(db_session, clock, lots=InventoryService(db_session))


@pytest.fixture(autouse=True)
async def _the_catalogue_knows_the_filament(db_session: AsyncSession) -> None:
    await InventoryService(db_session).create_spec(
        CreateMaterialSpec(code=MATERIAL, name="PLA Black", family="PLA")
    )


async def a_delivery_at(
    procurement: ProcurementService, clock: FixedClock, *, at: datetime, price: str | None
) -> None:
    """One order, paid, with one arrival of 1000 g at ``price`` on ``at``."""
    clock.set(at)
    order = await procurement.raise_order(
        CreatePurchaseOrder(
            lines=[
                CreatePurchaseLine(
                    kind=PurchasableKind.MATERIAL,
                    item_code=MATERIAL,
                    quantity=Decimal(1000),
                    unit="gram",
                )
            ]
        )
    )
    for stage in (PurchaseStatus.APPROVED, PurchaseStatus.PAID):
        await procurement.advance(order.id, stage)
    await procurement.receive(
        order.id,
        ReceiveDelivery(
            lines=[
                ReceiveLine(
                    line_id=order.lines[0].id,
                    quantity=Decimal(1000),
                    unit_price_paid=None if price is None else Decimal(price),
                )
            ]
        ),
        by=None,
    )


async def test_the_read_joins_receipts_to_their_lines_and_keeps_to_the_window(
    procurement: ProcurementService, clock: FixedClock, db_session: AsyncSession
) -> None:
    """Three arrivals over fourteen months; the year-long window drops the first.

    So «было» is the second arrival, not the oldest one on file — the window is
    a property of the read, and a panel titled «за год» that reached back
    further would be describing a different year.
    """
    now = datetime.fromisoformat("2026-09-24T12:00:00+00:00")
    await a_delivery_at(procurement, clock, at=now - timedelta(days=420), price="2.40")
    await a_delivery_at(procurement, clock, at=now - timedelta(days=300), price="1.80")
    await a_delivery_at(procurement, clock, at=now - timedelta(days=100), price=None)
    await a_delivery_at(procurement, clock, at=now - timedelta(days=10), price="1.50")

    prices = await price_movements(db_session, since=now - timedelta(days=365), until=now)

    [position] = prices.positions
    assert position.item_code == MATERIAL
    # The line was raised with no name, and the fold does not reach into the
    # catalogue for one: the code is what the buyer typed, and it is what shows.
    assert position.item_name == MATERIAL
    assert position.unit == "gram"
    assert position.earliest == Decimal("1.80")
    assert position.latest == Decimal("1.50")
    assert position.priced_receipts == 2
    assert position.unpriced_receipts == 1
    assert prices.since == now - timedelta(days=365)
    assert prices.until == now
