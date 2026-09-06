"""«Требуют заказа сейчас», and the column that has to stay empty.

The rule is pure — it takes the farm's thresholds as arguments rather than
reading them — so these cases state it in one line each. That the *delivery
layer* actually hands it the farm's own settings rather than a constant is proved
at the screen, in `tests/api/test_purchasing_api.py`, because that is where the
wiring lives and where it could be quietly replaced by a literal.

The load-bearing case here is `test_a_material_with_no_measured_consumption_
reports_no_coverage`. `design/purchasing.html` puts «1.2 месяца» in the
«Последствие» column, and nothing in this system measures material consumption:
`material_lots.remaining_grams` is written once, at lot creation, and never
decremented. A months figure could therefore only be derived from lot count or
from the roster — the flattering error ADR-0007 exists to stop, and one that
looks most reassuring exactly when coverage is worst.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.procurement import (
    ConsequenceKind,
    PurchasableKind,
    ReorderRow,
    StockedItem,
    needs_reorder,
    reorder_rows,
    seed_lines,
)

NO_COMMITMENTS: dict[str, tuple[Decimal, int]] = {}


def an_item(
    code: str = "PLA-BLACK", *, remaining: str = "100", threshold: str = "400"
) -> StockedItem:
    return StockedItem(
        kind=PurchasableKind.MATERIAL,
        item_code=code,
        item_name=code,
        remaining=Decimal(remaining),
        unit="gram",
        threshold=Decimal(threshold),
    )


def rows_for(
    *items: StockedItem,
    on_order: frozenset[str] = frozenset(),
    committed: dict[str, tuple[Decimal, int]] | None = None,
    auto_reorder: bool = True,
) -> list[ReorderRow]:
    return reorder_rows(
        items,
        on_order=on_order,
        committed=NO_COMMITMENTS if committed is None else committed,
        auto_reorder=auto_reorder,
    )


def test_a_material_with_no_measured_consumption_reports_no_coverage() -> None:
    """The ADR-0007 tripwire. If somebody adds a months figure, this fails.

    The consequence is a discriminated value and its coverage arm is *absent* —
    not null, not zero, not present-and-empty. A reader who wants to reintroduce
    «Покрытие 1.9 месяца» has to add the arm, which is the review the number never
    otherwise gets.
    """
    (row,) = rows_for(an_item())

    assert row.consequence.kind is ConsequenceKind.NOT_MEASURED
    assert row.consequence.committed_grams is None
    assert row.consequence.committed_jobs is None
    assert not any("month" in field for field in type(row.consequence).model_fields)


def test_queued_work_is_the_one_consequence_this_system_can_measure() -> None:
    """The honest form of the kit's «ORD-2152 ждёт»: grams the print queue has
    already promised away, read from `production.committed_material`."""
    (row,) = rows_for(an_item(), committed={"PLA-BLACK": (Decimal(820), 3)})

    assert row.consequence.kind is ConsequenceKind.COMMITTED_WORK
    assert row.consequence.committed_grams == Decimal(820)
    assert row.consequence.committed_jobs == 3


def test_a_material_committed_to_no_jobs_is_not_dressed_as_committed_work() -> None:
    """Zero jobs is "nothing is waiting", which is not a consequence — and a row
    saying «0 заказов ждёт» reads as a measurement of urgency that is not there."""
    (row,) = rows_for(an_item(), committed={"PLA-BLACK": (Decimal(0), 0)})

    assert row.consequence.kind is ConsequenceKind.NOT_MEASURED


def test_an_item_already_on_an_open_order_is_not_listed_again() -> None:
    """A buyer told twice about one shortage buys it twice."""
    assert rows_for(an_item(), on_order=frozenset({"PLA-BLACK"})) == []


def test_the_threshold_is_the_farms_number_and_not_a_constant() -> None:
    """Move the threshold and the same stock level changes side.

    `inventory.low_stock_grams` ships at 400 g and had no reader at all before
    this screen; a rule with 400 baked into it would pass every test here while
    ignoring the setting entirely.
    """
    plenty = an_item(remaining="300", threshold="150")
    short = an_item(remaining="300", threshold="400")

    assert rows_for(plenty) == []
    assert len(rows_for(short)) == 1


def test_the_farms_switch_silences_the_panel() -> None:
    """`inventory.auto_reorder` off means quiet, which is what the setting has
    always claimed to do and, until now, nothing honoured."""
    assert rows_for(an_item(), auto_reorder=False) == []


def test_a_threshold_of_zero_disables_the_rule_rather_than_urging_everything() -> None:
    """The convention `packaging_tara.reorder_at` already uses. Treating 0 as a
    live threshold would make every item with any stock at all overdue."""
    assert not needs_reorder(
        remaining=Decimal(0), low_at=Decimal(0), on_order=False, auto_reorder=True
    )


def test_the_emptiest_shelf_is_listed_first() -> None:
    """Ordered by shortfall as a *fraction* of the threshold, so a class counted
    in rolls and one counted in grams sort against each other sensibly."""
    rows = rows_for(
        an_item("PLA-BLACK", remaining="380", threshold="400"),
        an_item("PETG-RED", remaining="10", threshold="400"),
    )

    assert [row.item_code for row in rows] == ["PETG-RED", "PLA-BLACK"]


def test_seeding_an_order_from_the_list_quotes_no_prices() -> None:
    """«Собрать один заказ» raises lines nobody has asked a supplier about. A
    price here would enter an order total as though somebody had quoted it."""
    lines = seed_lines(rows_for(an_item(remaining="100", threshold="400")))

    assert [line.unit_price for line in lines] == [None]
    assert lines[0].quantity == Decimal(300)
    assert lines[0].unit == "gram"
