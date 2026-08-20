"""Projecting the loyalty ladder onto one customer.

The ladder itself is a price book and lives in `contexts.pricing`, where the
engine can read it without a database (ADR-0002). What is here is the *view* of
it: which rungs this customer has reached, and how much further the next one is.

Pure, and given the spend rather than fetching it. The account screen and the
order router therefore agree by construction — the badge that says «−4%» is
computed from the same ladder that took the four percent off.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from printorian.contexts.account.schemas import LadderStep, Tier
from printorian.contexts.pricing import LOYALTY_LADDER, next_step, step_for_spend

_HUNDRED = Decimal(100)


def tier_of(lifetime_spend: Decimal) -> Tier:
    """Where this much spending puts a customer on the ladder."""
    here = step_for_spend(lifetime_spend)
    above = next_step(lifetime_spend)

    steps = [
        LadderStep(
            code=step.code,
            from_spend=step.from_spend,
            discount_percent=step.discount_percent,
            reached=lifetime_spend >= step.from_spend,
        )
        for step in LOYALTY_LADDER
    ]

    if above is None:
        # Top of the ladder. No gap, and no bar — a progress bar with nothing
        # left to fill reads as "stuck at 100%" rather than "finished".
        return Tier(
            code=here.code,
            discount_percent=here.discount_percent,
            lifetime_spend=lifetime_spend,
            steps=steps,
        )

    # Measured against the whole ladder, not against the current rung. The kit
    # draws one continuous track with the rungs marked along it, so the fill has
    # to mean "distance travelled overall" — a per-rung percentage would jump
    # back to nothing every time somebody was promoted.
    top = LOYALTY_LADDER[-1].from_spend
    progress = (lifetime_spend / top * _HUNDRED).quantize(Decimal("0.1"), rounding=ROUND_DOWN)

    return Tier(
        code=here.code,
        discount_percent=here.discount_percent,
        lifetime_spend=lifetime_spend,
        steps=steps,
        next_code=above.code,
        next_from_spend=above.from_spend,
        to_next=above.from_spend - lifetime_spend,
        progress_percent=min(progress, _HUNDRED),
    )
