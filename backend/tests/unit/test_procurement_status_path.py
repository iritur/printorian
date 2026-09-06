"""«Путь заказа» — the only moves a purchase order is allowed to make.

Pure: no session, no clock. The table below is the whole rule, and it is written
out here rather than derived from `TRANSITIONS` — a test that recomputed the
table from the thing it is testing would agree with any edit, including a wrong
one.
"""

from __future__ import annotations

import pytest

from printorian.contexts.procurement import (
    PIPE,
    TRANSITIONS,
    PurchaseStatus,
    assert_transition,
    can_transition,
)
from printorian.core.errors import ValidationError

S = PurchaseStatus

#: What each stage may become, written out independently of the source.
#:
#: Nothing steps backwards: an order that was paid and then un-paid is a refund,
#: which is a different record with different money in it, not this row moving
#: left. Everything short of the two terminal states may be cancelled, because a
#: supplier can fall through at any point up to the goods being on the shelf.
EXPECTED: dict[PurchaseStatus, set[PurchaseStatus]] = {
    S.DRAFT: {S.APPROVED, S.CANCELLED},
    S.APPROVED: {S.PAID, S.CANCELLED},
    # Straight to RECEIVING as well as through IN_TRANSIT: a courier who arrives
    # before anybody updated the order is the common case, not the odd one.
    S.PAID: {S.IN_TRANSIT, S.RECEIVING, S.CANCELLED},
    S.IN_TRANSIT: {S.RECEIVING, S.CANCELLED},
    S.RECEIVING: {S.STORED, S.CANCELLED},
    S.STORED: set(),
    S.CANCELLED: set(),
}


@pytest.mark.parametrize("current", list(PurchaseStatus), ids=[s.value for s in PurchaseStatus])
def test_every_legal_transition_is_allowed_and_no_others(current: PurchaseStatus) -> None:
    """Both halves matter, and the second is the one that catches a widening.

    Asserting only that the legal moves are permitted would pass a table that
    permitted everything, which is how a stage machine stops being one.
    """
    permitted = {target for target in PurchaseStatus if can_transition(current, target)}

    assert permitted == EXPECTED[current]


def test_a_terminal_order_goes_nowhere() -> None:
    """Stored and cancelled are the ends. A cancelled order is not revived by
    moving it back to draft — it is a new order, with a new number, that somebody
    decided to raise."""
    assert TRANSITIONS[S.STORED] == frozenset()
    assert TRANSITIONS[S.CANCELLED] == frozenset()
    assert S.STORED.is_terminal and S.CANCELLED.is_terminal


def test_an_illegal_jump_names_both_states_in_its_code() -> None:
    """ADR-0012: a code with structured details, never prose.

    Both ends go in the details because the console renders «Нельзя перейти из
    "Черновик" сразу в "На складе"» from them; a sentence here would be one the
    client had to take apart again.
    """
    with pytest.raises(ValidationError) as raised:
        assert_transition(S.DRAFT, S.STORED)

    assert raised.value.code == "error.procurement.illegal_transition"
    assert raised.value.details["from"] == "draft"
    assert raised.value.details["to"] == "stored"
    assert raised.value.details["allowed"] == ["approved", "cancelled"]


def test_a_legal_move_says_nothing_at_all() -> None:
    assert assert_transition(S.PAID, S.RECEIVING) is None


def test_the_pipe_draws_the_six_stages_and_not_cancellation() -> None:
    """A cancelled order did not reach a seventh step — it stopped at whichever
    one it was on. Drawing a seventh box would tell a reader the opposite, so the
    console renders the cancellation from the status instead."""
    assert PIPE == (S.DRAFT, S.APPROVED, S.PAID, S.IN_TRANSIT, S.RECEIVING, S.STORED)
    assert S.CANCELLED not in PIPE
