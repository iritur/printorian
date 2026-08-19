"""The lifetime ladder: what a returning customer has earned.

Separate from :mod:`printorian.contexts.pricing.discounts`, which is the *volume*
ladder — a discount for ordering fifty of something at once. This one is a
discount for having ordered for a year, and the two compose: a Silver customer
ordering fifty parts gets both, as two named lines in the breakdown.

**Why this is here and not in `account`.** The screen that shows the ladder is the
customer's own; the ladder itself is a price book, and price books live in
pricing where the engine can read them without a database (ADR-0002). The account
context knows a lifetime total and asks this module what it means.

The step boundaries are a commercial decision, not a derived fact. They are stated
once, here, in roubles of lifetime spend:

============  ==============  ==========
Step          From            Discount
============  ==============  ==========
``standard``  0 ₽             0 %
``silver``    100 000 ₽       4 %
``gold``      300 000 ₽       8 %
============  ==============  ==========

Lifetime spend counts orders that were actually paid for and not refunded — see
``OrderingService.lifetime``. Counting cancelled orders would let anyone reach
Gold by placing and abandoning thirty of them.

No cliff guard is needed, unlike the volume ladder. That guard exists because a
step there can make *the same order* cheaper at fifty units than at forty-nine;
here the input is history, which only ever grows, so crossing a step can never
reduce what an earlier order cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from printorian.contexts.pricing.rates import CustomerTier


@dataclass(frozen=True, slots=True)
class LoyaltyStep:
    """One rung: the spend that reaches it and the discount it carries."""

    code: str
    from_spend: Decimal
    discount_percent: Decimal


#: Ascending by ``from_spend``. ``step_for_spend`` relies on the order.
LOYALTY_LADDER: tuple[LoyaltyStep, ...] = (
    LoyaltyStep(code="standard", from_spend=Decimal(0), discount_percent=Decimal(0)),
    LoyaltyStep(code="silver", from_spend=Decimal(100_000), discount_percent=Decimal(4)),
    LoyaltyStep(code="gold", from_spend=Decimal(300_000), discount_percent=Decimal(8)),
)


def step_for_spend(lifetime: Decimal) -> LoyaltyStep:
    """The highest rung this much spending reaches. Never ``None`` — the first is 0 ₽."""
    reached = LOYALTY_LADDER[0]
    for step in LOYALTY_LADDER:
        if lifetime >= step.from_spend:
            reached = step
        else:
            break
    return reached


def next_step(lifetime: Decimal) -> LoyaltyStep | None:
    """The rung above, or ``None`` at the top of the ladder."""
    return next((step for step in LOYALTY_LADDER if lifetime < step.from_spend), None)


def tier_for_spend(lifetime: Decimal) -> CustomerTier:
    """The price book to hand the engine for a customer who has spent this much.

    The engine takes a :class:`CustomerTier` and knows nothing about ladders, which
    is what keeps a negotiated tier — a contract with one customer — expressible in
    exactly the same terms as an earned one.
    """
    step = step_for_spend(lifetime)
    return CustomerTier(code=step.code, discount_percent=step.discount_percent)
