"""What a purchase order is allowed to do, and what the farm is allowed to buy.

Pure: no session, no clock, no I/O. The reorder predicate takes its thresholds as
arguments rather than reading them, because those four numbers are the farm's own
settings (`inventory.low_stock_grams` and friends) and resolving them here would
put a database read inside a rule the tests want to state in one line.

The six stages are the ones `design/purchasing.html` draws in its «Путь заказа»
pipe, plus `CANCELLED`. They are stages of *the farm's* commitment, not of the
supplier's shipment: «Оплачен» is a fact about money leaving, «В пути» a fact the
supplier asserted, «Приёмка» the moment somebody is standing at the door with a
box. Collapsing any two of them would lose a question a buyer actually asks.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from printorian.core.errors import ValidationError


class PurchaseStatus(StrEnum):
    """Where one purchase order is between «Черновик» and «На складе»."""

    #: Raised, nothing committed. The kit's draft has no supplier yet — that is
    #: why `purchase_orders.supplier_id` is nullable rather than defaulted.
    DRAFT = "draft"
    #: Somebody with the money said yes. Still nothing has been paid.
    APPROVED = "approved"
    PAID = "paid"
    #: The supplier says it has left. The farm has not seen it.
    IN_TRANSIT = "in_transit"
    #: A box is here and is being counted against what was ordered. Its own state
    #: because a part-received order is neither in transit nor stored, and calling
    #: it either would answer "where is my filament" wrongly.
    RECEIVING = "receiving"
    STORED = "stored"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {PurchaseStatus.STORED, PurchaseStatus.CANCELLED}


#: Stages a caller may still be waiting for stock from. What
#: `reads.ordered_codes` counts, and therefore what makes a material read
#: «Заказан» on the materials table.
#:
#: `DRAFT` is deliberately absent: a draft is a thought, and letting one suppress
#: the reorder row would mean an order nobody ever approved quietly stopped the
#: farm from being told it is out of filament.
OPEN_STATUSES: frozenset[PurchaseStatus] = frozenset(
    {
        PurchaseStatus.APPROVED,
        PurchaseStatus.PAID,
        PurchaseStatus.IN_TRANSIT,
        PurchaseStatus.RECEIVING,
    }
)


#: The only moves the system performs. Anything else is a bug rather than a
#: business decision, and is refused loudly instead of half-applied.
#:
#: Every non-terminal stage may be cancelled, because a supplier can fall through
#: at any point up to the moment the goods are on the shelf. Nothing steps
#: backwards: an order that was paid and then un-paid is a refund, which is a
#: different record with different money in it, not this row moving left.
TRANSITIONS: dict[PurchaseStatus, frozenset[PurchaseStatus]] = {
    PurchaseStatus.DRAFT: frozenset({PurchaseStatus.APPROVED, PurchaseStatus.CANCELLED}),
    PurchaseStatus.APPROVED: frozenset({PurchaseStatus.PAID, PurchaseStatus.CANCELLED}),
    # Straight to `RECEIVING` as well as through `IN_TRANSIT`: a courier who
    # arrives before anybody updated the order is the common case, not the odd one.
    PurchaseStatus.PAID: frozenset(
        {PurchaseStatus.IN_TRANSIT, PurchaseStatus.RECEIVING, PurchaseStatus.CANCELLED}
    ),
    PurchaseStatus.IN_TRANSIT: frozenset({PurchaseStatus.RECEIVING, PurchaseStatus.CANCELLED}),
    PurchaseStatus.RECEIVING: frozenset({PurchaseStatus.STORED, PurchaseStatus.CANCELLED}),
    PurchaseStatus.STORED: frozenset(),
    PurchaseStatus.CANCELLED: frozenset(),
}


class PurchasableKind(StrEnum):
    """The five classes `design/purchasing.html` buys, all declared at once.

    All five, though this slice can only *receive* one of them, so that adding
    receiving for the next class is a service change rather than a migration that
    rewrites a CHECK constraint on a table with orders in it. A buyer can already
    record what was ordered from whom in every class — that is the whole point of
    the screen — and only the step that increments a stock model is gated.
    """

    MATERIAL = "material"
    PRINTER = "printer"
    SPARE_PART = "spare_part"
    PACKAGING = "packaging"
    POST_CONSUMABLE = "post_consumable"


#: Classes whose arrival this build can actually put into stock.
#:
#: Only materials, and the reason is ADR-0007 rather than effort. `packaging.Tara`
#: and `postproduction.Consumable` both carry stock, but neither context offers an
#: *increment* — `PackingCatalogue.stock_tara` restates the level absolutely — so
#: receiving into them would mean this context computing a new level from one it
#: read a moment ago, and losing whatever a packer did in between. Spare parts have
#: no stock model at all, and issue #33 claims that table; modelling one here would
#: give the farm two.
#:
#: A line in one of the other four classes is still ordered, tracked and paid for.
#: Only the arrival is refused, with `error.procurement.class_not_receivable` —
#: an honest refusal rather than a silent no-op that would leave a buyer believing
#: a box of film had reached the shelf.
RECEIVABLE: frozenset[PurchasableKind] = frozenset({PurchasableKind.MATERIAL})


def can_transition(current: PurchaseStatus, target: PurchaseStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: PurchaseStatus, target: PurchaseStatus) -> None:
    """Refuse an illegal stage jump, naming both ends (ADR-0012).

    Both states go in the details rather than into a sentence, because the console
    renders «Нельзя перейти из "Черновик" сразу в "На складе"» from them and a
    prose message here would be one the client had to parse back apart.
    """
    if not can_transition(current, target):
        raise ValidationError(
            "error.procurement.illegal_transition",
            **{
                "from": current.value,
                "to": target.value,
                "allowed": sorted(state.value for state in TRANSITIONS[current]),
            },
        )


def needs_reorder(
    *, remaining: Decimal, low_at: Decimal, on_order: bool, auto_reorder: bool
) -> bool:
    """Whether «Требуют заказа сейчас» should carry this item.

    ``on_order`` suppresses the row, so a buyer is not told twice about a thing
    already coming. ``auto_reorder`` is the farm's own switch
    (`inventory.auto_reorder`): with it off the panel goes quiet rather than
    nagging, which is what the setting has always claimed to do and, until this
    slice, nothing read.

    ``low_at`` of zero disables the threshold entirely — the same convention
    `packaging_tara.reorder_at` uses — rather than making every item with any
    stock at all urgent.
    """
    if not auto_reorder or on_order or low_at <= 0:
        return False
    return remaining <= low_at
