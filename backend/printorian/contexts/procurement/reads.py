"""What the purchasing screen reads, and the one thing the rest of the farm reads.

`ordered_codes` is the point of contact. `material_specs.has_open_order` used to
carry "is this on order", set by hand and described by its own comment as a
placeholder until purchase orders existed. They exist now, so the answer is
computed from them and the column is gone: one fact, one place, and no way for a
flag somebody forgot to clear to keep a material reading «Заказан» for a month.

The rest of this module is the board. It is deliberately thin and mostly pure —
the reorder list is a *pure* function over stock levels the delivery layer
gathered, because the five purchasable classes live in four different contexts
and a read here that reached into each of them would make `procurement` depend on
all four. Composition across contexts is the API layer's job (ARCHITECTURE
§layering), which is also why `on_order` and `committed` are required keyword
arguments below rather than defaulted: a caller that forgot to ask production
would otherwise get "nothing is committed" instead of an error, and a silently
flattering answer is the ADR-0007 failure this repository keeps repeating.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import MaterialSpecView
from printorian.contexts.procurement.models import PurchaseOrder, PurchaseOrderLine, Supplier
from printorian.contexts.procurement.policies import (
    OPEN_STATUSES,
    PurchasableKind,
    PurchaseStatus,
    needs_reorder,
)
from printorian.contexts.procurement.schemas import (
    ConsequenceKind,
    CreatePurchaseLine,
    PurchaseOrderRow,
    PurchaseStatusCount,
    ReorderConsequence,
    ReorderRow,
)

#: Grams are how filament is stocked, priced and queued everywhere else in this
#: system, so the reorder row states the unit rather than leaving a bare number.
MATERIAL_UNIT = "gram"


@dataclass(frozen=True, slots=True, kw_only=True)
class StockedItem:
    """One thing with a level and a threshold, from whichever context stocks it.

    The shape every purchasable class is flattened into before the reorder rule
    is applied, so the rule is written once instead of once per class. Building
    one of these is the caller's admission that it *measured* the level — an item
    whose stock is unknown is left out entirely rather than passed in at zero.
    """

    kind: PurchasableKind
    item_code: str
    item_name: str
    remaining: Decimal
    unit: str
    threshold: Decimal
    #: Months of cover, when the stocking context genuinely measures consumption.
    #:
    #: Packing does — `packaging_task_tara` is a ledger of every box actually
    #: used — so a tara row can honestly say «1.3 месяца». Materials do not:
    #: nothing decrements `material_lots.remaining_grams`, so this stays null for
    #: filament and the console draws an em dash. The asymmetry is the measurement
    #: itself, not an omission to be tidied up later.
    coverage_months: Decimal | None = None


def material_items(
    rows: Sequence[MaterialSpecView], *, low_at: Decimal
) -> list[StockedItem]:
    """Flatten the materials table into stock items the reorder rule can read.

    Every active spec, including the ones at zero — which is why this reads the
    materials table rather than `inventory.headroom`, whose own docstring says it
    omits materials with nothing left. On a purchasing screen the empty ones are
    the entire point.
    """
    return [
        StockedItem(
            kind=PurchasableKind.MATERIAL,
            item_code=row.code,
            item_name=row.name,
            remaining=row.total_remaining_grams,
            unit=MATERIAL_UNIT,
            threshold=low_at,
        )
        for row in rows
    ]


def reorder_rows(
    items: Sequence[StockedItem],
    *,
    on_order: frozenset[str],
    committed: Mapping[str, tuple[Decimal, int]],
    auto_reorder: bool,
) -> list[ReorderRow]:
    """«Требуют заказа сейчас», worst first.

    ``committed`` maps a material code to the grams and job count the print queue
    has already promised it (`production.committed_material`). It is the only
    measured basis this system has for the kit's «ORD-2152 ждёт», and it is the
    only thing that fills the «Последствие» column with anything but an em dash —
    see `ConsequenceKind` for why there is no months figure for filament.
    """
    rows = [
        ReorderRow(
            kind=item.kind,
            item_code=item.item_code,
            item_name=item.item_name,
            remaining=item.remaining,
            unit=item.unit,
            threshold=item.threshold,
            consequence=_consequence(item, committed),
        )
        for item in items
        if needs_reorder(
            remaining=item.remaining,
            low_at=item.threshold,
            on_order=item.item_code in on_order,
            auto_reorder=auto_reorder,
        )
    ]
    # Emptiest first, in units of "how far under the threshold", so a class
    # counted in rolls and one counted in grams sort against each other sensibly.
    rows.sort(key=lambda row: _shortfall(row), reverse=True)
    return rows


def _shortfall(row: ReorderRow) -> Decimal:
    """How far under its own threshold a row is, as a fraction of that threshold."""
    if row.threshold <= 0:
        return Decimal(0)
    return (row.threshold - row.remaining) / row.threshold


def _consequence(
    item: StockedItem, committed: Mapping[str, tuple[Decimal, int]]
) -> ReorderConsequence:
    """What running out of this costs, stated only in terms somebody measured."""
    promised = committed.get(item.item_code)
    if promised is not None and promised[1] > 0:
        grams, jobs = promised
        return ReorderConsequence(
            kind=ConsequenceKind.COMMITTED_WORK, committed_grams=grams, committed_jobs=jobs
        )
    return ReorderConsequence(kind=ConsequenceKind.NOT_MEASURED)


def seed_lines(rows: Sequence[ReorderRow]) -> list[CreatePurchaseLine]:
    """«Собрать один заказ» — the reorder list, as draft lines.

    Quantities are the shortfall, and there is no price on any of them: nobody has
    asked a supplier yet, and a line carrying a guess would put an invented figure
    straight into an order total.
    """
    return [
        CreatePurchaseLine(
            kind=row.kind,
            item_code=row.item_code,
            item_name=row.item_name,
            quantity=max(row.threshold - row.remaining, Decimal(1)),
            unit=row.unit,
        )
        for row in rows
    ]


# ------------------------------------------------------------- the order side


async def ordered_codes(db: AsyncSession) -> frozenset[str]:
    """Item codes sitting on an open purchase order.

    What replaced `material_specs.has_open_order`. A draft does not count
    (`policies.OPEN_STATUSES`): a draft nobody approved must not stop the farm
    being told it is out of filament.
    """
    rows = await db.scalars(
        select(PurchaseOrderLine.item_code)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.order_id)
        .where(PurchaseOrder.status.in_(OPEN_STATUSES))
        .distinct()
    )
    return frozenset(rows)


async def order_rows(db: AsyncSession, *, limit: int = 200) -> list[PurchaseOrderRow]:
    """The orders table — newest first, and carrying no money.

    The line count and unit total are aggregated in SQL rather than by loading
    every line: the table shows «2 позиции · 32 единицы» per row, and fetching the
    lines to add them up would be one query per order on every refresh.
    """
    totals = (
        select(
            PurchaseOrderLine.order_id.label("order_id"),
            func.count(PurchaseOrderLine.id).label("line_count"),
            func.coalesce(func.sum(PurchaseOrderLine.quantity), 0).label("quantity"),
        )
        .group_by(PurchaseOrderLine.order_id)
        .subquery()
    )
    rows = await db.execute(
        select(PurchaseOrder, Supplier, totals.c.line_count, totals.c.quantity)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .outerjoin(totals, totals.c.order_id == PurchaseOrder.id)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(limit)
    )
    return [
        PurchaseOrderRow(
            id=order.id,
            number=order.number,
            status=order.status,
            supplier_code=supplier.code if supplier else None,
            supplier_name=supplier.name if supplier else None,
            line_count=int(count or 0),
            total_quantity=Decimal(str(quantity or 0)),
            expected_at=order.expected_at,
            created_at=order.created_at,
        )
        for order, supplier, count, quantity in rows.all()
    ]


async def status_counts(db: AsyncSession) -> list[PurchaseStatusCount]:
    """The filter chips. Every status appears, including the empty ones.

    A chip reading «Принят 0» is information; a missing chip is a gap a person has
    to notice — the same reason `inventory.table` emits all four of its statuses.
    """
    rows = await db.execute(
        select(PurchaseOrder.status, func.count(PurchaseOrder.id)).group_by(PurchaseOrder.status)
    )
    tally = {status: int(count or 0) for status, count in rows.all()}
    return [
        PurchaseStatusCount(status=status, count=tally.get(status, 0)) for status in PurchaseStatus
    ]


__all__ = [
    "MATERIAL_UNIT",
    "StockedItem",
    "material_items",
    "order_rows",
    "ordered_codes",
    "reorder_rows",
    "seed_lines",
    "status_counts",
]
