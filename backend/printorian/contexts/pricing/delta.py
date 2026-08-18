"""Option deltas — the scenario's "+120 in labor, -260 in material".

This is the whole payoff of a pure engine: price the configuration twice and
subtract. There is no separate "what would this option cost" code path that could
drift away from the real calculation, because there is no separate code path.
"""

from __future__ import annotations

from printorian.contexts.pricing.breakdown import (
    Breakdown,
    BreakdownDelta,
    Category,
    LineDelta,
)
from printorian.contexts.pricing.engine import price
from printorian.contexts.pricing.rates import CustomerTier, RateSnapshot
from printorian.contexts.pricing.spec import PriceSpec
from printorian.core.errors import ValidationError


def diff(before: Breakdown, after: Breakdown) -> BreakdownDelta:
    """Compare two breakdowns line by line.

    Lines present in only one side compare against zero, so an option that adds a
    finish shows up as a new line rather than vanishing from the summary.
    """
    if before.currency is not after.currency:
        raise ValidationError(
            "error.pricing.delta_currency_mismatch",
            before=str(before.currency),
            after=str(after.currency),
        )

    # dict preserves insertion order and de-duplicates: "before" lines keep their
    # original order, then any line that only "after" has is appended.
    categories: dict[str, Category] = {line.code: line.category for line in before.lines}
    for line in after.lines:
        categories.setdefault(line.code, line.category)
    codes = list(categories)

    # `amount_of` already returns zero for a code the breakdown does not have, which
    # is exactly the semantics a delta wants: an added line was previously nothing.
    lines = tuple(
        LineDelta(
            code=code,
            category=categories[code],
            before=before.amount_of(code),
            after=after.amount_of(code),
        )
        for code in codes
    )

    return BreakdownDelta(
        lines=lines,
        currency=before.currency,
        total_before=before.total,
        total_after=after.total,
        unit_before=before.unit_price,
        unit_after=after.unit_price,
        # Comparing across engine versions or rate snapshots is legitimate for an
        # audit, but the difference then includes a rule change and not just the
        # customer's choice. Flagged so the UI can say so.
        comparable=(
            before.engine_version == after.engine_version
            and before.rate_snapshot_id == after.rate_snapshot_id
        ),
    )


def preview(
    spec: PriceSpec,
    rates: RateSnapshot,
    tier: CustomerTier | None = None,
    **changes: object,
) -> BreakdownDelta:
    """Price ``spec``, then price it again with ``changes`` applied, and compare.

    This is what the configurator calls on every option toggle::

        preview(spec, rates, quantity=10)
        preview(spec, rates, rush=True)
        preview(spec, rates, material=other_material)
    """
    return diff(price(spec, rates, tier), price(spec.with_changes(**changes), rates, tier))
