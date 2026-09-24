"""«Поставщики» — the scorecard, computed from what arrived and never typed in.

Issue #34's last clause: *the supplier scorecard computes from delivered POs
rather than being entered by hand*. Three properties make that computation an
honest one rather than merely a present one, and each is a case below:

* **Only a delivery counts.** A cancelled order is not a late one, an order still
  in transit is not an early one, and a box being counted at the door may yet be
  refused. `deliveries` is `STORED` and nothing else.

* **The denominator of «В срок» is the dated deliveries, not the deliveries.**
  An order raised without an `expected_at` was neither punctual nor late. Put it
  under the line and every date the buyer forgot to type marks the supplier down;
  put it over the line and it marks them up. It goes in neither, and a supplier
  whose deliveries were all undated reads «—» (CLAUDE.md §1).

* **A supplier nothing has come from is on the table with zeros and a null**, not
  missing from it — the chips rule. A missing row is a gap somebody has to notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import InventoryService
from printorian.contexts.procurement import (
    CreatePurchaseLine,
    CreatePurchaseOrder,
    CreateSupplier,
    ProcurementService,
    PurchasableKind,
    PurchaseStatus,
    SupplierScore,
    on_time_share,
    supplier_scores,
)
from printorian.core.clock import FixedClock
from printorian.core.ids import EntityId

# ------------------------------------------------------------------ the rule


def test_nothing_dated_is_not_a_share_at_all() -> None:
    """Null, not zero and not one: a supplier with undated deliveries has no
    punctuality record, which is a different fact from a bad one."""
    assert on_time_share(dated=0, on_time=0) is None


@pytest.mark.parametrize(
    ("dated", "on_time", "expected"),
    [
        (1, 1, Decimal("1.0000")),
        (2, 1, Decimal("0.5000")),
        (3, 1, Decimal("0.3333")),
        (4, 0, Decimal("0.0000")),
    ],
)
def test_the_share_is_on_time_over_dated_to_four_places(
    dated: int, on_time: int, expected: Decimal
) -> None:
    assert on_time_share(dated=dated, on_time=on_time) == expected


def test_more_on_time_than_dated_is_a_bug_not_a_score() -> None:
    """Refused rather than clamped to 100%: a numerator larger than its own
    denominator means the two counts came from different row sets."""
    with pytest.raises(ValueError, match="outside"):
        on_time_share(dated=1, on_time=2)


# ------------------------------------------------------------------ the read

MATERIAL = "PLA-BLACK"
RAISED = datetime.fromisoformat("2026-09-01T09:00:00+00:00")


@pytest.fixture
def procurement(db_session: AsyncSession, clock: FixedClock) -> ProcurementService:
    return ProcurementService(db_session, clock, lots=InventoryService(db_session))


async def raise_to(
    procurement: ProcurementService,
    clock: FixedClock,
    *,
    supplier_id: EntityId | None,
    status: PurchaseStatus,
    expected_at: datetime | None,
    stored_at: datetime | None = None,
) -> None:
    """One order, dated or not, walked to ``status`` with the clock moved so that
    ``stored_at`` is the moment it reached the shelf."""
    clock.set(RAISED)
    order = await procurement.raise_order(
        CreatePurchaseOrder(
            expected_at=expected_at,
            lines=[
                CreatePurchaseLine(
                    kind=PurchasableKind.MATERIAL,
                    item_code=MATERIAL,
                    quantity=Decimal(1000),
                    unit="gram",
                )
            ],
        )
    )
    if supplier_id is not None:
        await procurement.assign_supplier(order.id, supplier_id)
    path: dict[PurchaseStatus, tuple[PurchaseStatus, ...]] = {
        PurchaseStatus.PAID: (PurchaseStatus.APPROVED, PurchaseStatus.PAID),
        PurchaseStatus.RECEIVING: (
            PurchaseStatus.APPROVED,
            PurchaseStatus.PAID,
            PurchaseStatus.RECEIVING,
        ),
        PurchaseStatus.CANCELLED: (PurchaseStatus.APPROVED, PurchaseStatus.CANCELLED),
        PurchaseStatus.STORED: (
            PurchaseStatus.APPROVED,
            PurchaseStatus.PAID,
            PurchaseStatus.RECEIVING,
            PurchaseStatus.STORED,
        ),
    }
    for stage in path[status]:
        if stage is PurchaseStatus.STORED:
            assert stored_at is not None
            clock.set(stored_at)
        await procurement.advance(order.id, stage)


def score_of(scores: list[SupplierScore], code: str) -> SupplierScore:
    found = [score for score in scores if score.code == code]
    assert len(found) == 1, f"{code} appears {len(found)} times"
    return found[0]


async def test_the_scorecard_counts_only_what_reached_the_shelf_and_dates_only_what_was_dated(
    procurement: ProcurementService, clock: FixedClock, db_session: AsyncSession
) -> None:
    """Five orders from one supplier, and only three of them are deliveries.

    Of the three, two carried a date: one kept it, one missed it by two days.
    The third arrived undated, so it is in `deliveries` and in nothing else —
    which is what makes the share 1/2 rather than 1/3 or 2/3.
    """
    supplier = await procurement.add_supplier(
        CreateSupplier(code="FILAMENT-RU", name="ТехноПласт", kinds=[PurchasableKind.MATERIAL])
    )
    due = RAISED + timedelta(days=9)
    # Kept its date.
    await raise_to(
        procurement,
        clock,
        supplier_id=supplier.id,
        status=PurchaseStatus.STORED,
        expected_at=due,
        stored_at=due - timedelta(days=1),
    )
    # Missed it.
    await raise_to(
        procurement,
        clock,
        supplier_id=supplier.id,
        status=PurchaseStatus.STORED,
        expected_at=due,
        stored_at=due + timedelta(days=2),
    )
    # Arrived, and nobody had typed a date — the most recent delivery of the three.
    await raise_to(
        procurement,
        clock,
        supplier_id=supplier.id,
        status=PurchaseStatus.STORED,
        expected_at=None,
        stored_at=due + timedelta(days=5),
    )
    # Neither of these is a delivery, late or otherwise.
    await raise_to(
        procurement,
        clock,
        supplier_id=supplier.id,
        status=PurchaseStatus.CANCELLED,
        expected_at=due,
    )
    await raise_to(
        procurement,
        clock,
        supplier_id=supplier.id,
        status=PurchaseStatus.RECEIVING,
        expected_at=due - timedelta(days=30),
    )

    score = score_of(await supplier_scores(db_session), "FILAMENT-RU")

    assert score.name == "ТехноПласт"
    assert score.kinds == ["material"]
    assert score.deliveries == 3
    assert score.dated == 2
    assert score.on_time == 1
    assert score.on_time_share == Decimal("0.5000")
    assert score.last_delivery_at == due + timedelta(days=5)


async def test_a_supplier_with_nothing_delivered_is_listed_with_a_null_share(
    procurement: ProcurementService, clock: FixedClock, db_session: AsyncSession
) -> None:
    """Zeros for the counts, because zero deliveries *is* the measurement; null
    for the share, because 0/0 is not one. And an order that names no supplier
    belongs to nobody's record rather than to the first row's."""
    await procurement.add_supplier(CreateSupplier(code="NEW-ONE", name="Новый"))
    await raise_to(
        procurement,
        clock,
        supplier_id=None,
        status=PurchaseStatus.STORED,
        expected_at=RAISED + timedelta(days=1),
        stored_at=RAISED + timedelta(days=1),
    )

    scores = await supplier_scores(db_session)

    assert [score.code for score in scores] == ["NEW-ONE"]
    assert scores[0].deliveries == 0
    assert scores[0].dated == 0
    assert scores[0].on_time == 0
    assert scores[0].on_time_share is None
    assert scores[0].last_delivery_at is None


async def test_undated_deliveries_alone_score_a_dash_rather_than_a_percentage(
    procurement: ProcurementService, clock: FixedClock, db_session: AsyncSession
) -> None:
    """Two deliveries, no dates: the supplier has a delivery record and no
    punctuality record, and the row says exactly that."""
    supplier = await procurement.add_supplier(CreateSupplier(code="UNDATED", name="Без дат"))
    for days in (3, 6):
        await raise_to(
            procurement,
            clock,
            supplier_id=supplier.id,
            status=PurchaseStatus.STORED,
            expected_at=None,
            stored_at=RAISED + timedelta(days=days),
        )

    score = score_of(await supplier_scores(db_session), "UNDATED")

    assert score.deliveries == 2
    assert score.dated == 0
    assert score.on_time_share is None
