"""«Оборачиваемость» and «Залежалое», folded from the movement ledger.

Issue #35's second slice. The two rules that make the figures honest:

* **turnover counts lots that left the shelf**; a lot still there is beside the
  mean, never inside it, so an untouched shelf does not read as a fast one;
* **dead stock is costed only where receiving recorded a price**; the unpriced
  lots are counted and add nothing, rather than being costed at nought.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    MOVED_MOUNTED,
    MOVED_MOVED,
    MOVED_RECEIVED,
    LocationKind,
    LotHistory,
    dead_stock,
    lot_histories,
    turnover,
)
from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.inventory.placement import record_movement
from printorian.core.ids import new_id
from tests.factories import ensure_lot

T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
NOW = T0 + timedelta(days=90)


def a_lot(
    *,
    family: str = "PLA",
    received: datetime | None = T0,
    left: datetime | None = None,
    last: datetime | None = None,
    remaining: str = "600",
    price: str | None = "1800",
    kind: LocationKind = LocationKind.STOCK,
) -> LotHistory:
    return LotHistory(
        lot_id=new_id(),
        label=f"{family}-001",
        family=family,
        location_kind=kind,
        initial_grams=Decimal(1000),
        remaining_grams=Decimal(remaining),
        purchase_price=None if price is None else Decimal(price),
        received_at=received,
        left_at=left,
        last_moved_at=last or left or received,
    )


# ------------------------------------------------------------- turnover


def test_turnover_is_the_mean_over_lots_that_left_and_counts_the_rest_beside_it() -> None:
    rows = turnover(
        [
            a_lot(left=T0 + timedelta(days=4)),
            a_lot(left=T0 + timedelta(days=10)),
            a_lot(),  # still on the shelf: counted beside, not inside
            a_lot(family="PETG"),
            a_lot(received=None, left=T0 + timedelta(days=1)),  # predates the ledger
        ],
        since=T0 - timedelta(days=1),
        until=NOW,
    )

    assert [(r.family, r.turned, r.mean_days_on_shelf, r.still_on_shelf) for r in rows] == [
        ("PETG", 0, None, 1),
        ("PLA", 2, Decimal("7.0"), 1),
    ]


def test_turnover_keeps_to_the_window_of_receipt() -> None:
    rows = turnover(
        [a_lot(received=T0 - timedelta(days=30), left=T0)],
        since=T0 - timedelta(days=1),
        until=NOW,
    )
    assert rows == []


# ------------------------------------------------------------- dead stock


def test_dead_stock_costs_only_priced_lots_and_counts_the_others() -> None:
    report = dead_stock(
        [
            a_lot(remaining="600", price="1800"),  # 60% left of 1800 → 1080
            a_lot(remaining="250", price=None),
            a_lot(remaining="0", price="900"),  # nothing left: not dead, just gone
            a_lot(remaining="400", last=NOW - timedelta(days=3)),  # moved lately
            a_lot(remaining="500", kind=LocationKind.PRINTER),  # on a machine, not a shelf
            a_lot(received=None, last=None),  # nothing says how long it sat
        ],
        now=NOW,
        idle_days=60,
    )

    assert [lot.remaining_grams for lot in report.lots] == [Decimal(600), Decimal(250)]
    assert report.lots[0].value == Decimal("1080.00")
    assert report.lots[0].idle_days == Decimal("90.0")
    assert report.lots[1].value is None
    assert report.total_grams == Decimal(850)
    assert report.total_value == Decimal("1080.00")
    assert report.unpriced_lots == 1


# ------------------------------------------------------------- the read


async def test_the_read_takes_the_three_instants_from_the_ledger(
    db_session: AsyncSession,
) -> None:
    lot_id = new_id()
    await ensure_lot(db_session, lot_id)
    lot = await db_session.get(MaterialLot, lot_id)
    assert lot is not None
    lot.purchase_price = Decimal("1500")
    for reason, at in (
        (MOVED_RECEIVED, T0),
        (MOVED_MOVED, T0 + timedelta(days=2)),
        (MOVED_MOUNTED, T0 + timedelta(days=5)),
    ):
        await record_movement(db_session, lot, reason=reason, at=at)
        # `record_movement` does not flush, and `next_sequence` reads the ledger:
        # two rows in one flush would claim the same rung.
        await db_session.flush()

    [history] = [h for h in await lot_histories(db_session) if h.lot_id == lot_id]

    assert history.received_at == T0
    # The first *outbound* row, not the cell-to-cell move two days earlier.
    assert history.left_at == T0 + timedelta(days=5)
    assert history.last_moved_at == T0 + timedelta(days=5)
    assert history.purchase_price == Decimal("1500")
