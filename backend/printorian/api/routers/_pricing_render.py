"""Turning priced breakdowns into JSON.

Split out of `pricing.py` purely by responsibility: the endpoints decide *what*
to price, these functions decide how the answer is spelled on the wire. Both
obey ADR-0012 — codes and numbers leave the backend, never prose, because the
client is the only place that knows which language the reader wants.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from printorian.contexts.pricing import Breakdown, BreakdownDelta


def _render(breakdown: Breakdown) -> dict[str, Any]:
    """Serialize a breakdown as codes and numbers — never prose (ADR-0012)."""
    return {
        "currency": breakdown.currency.value,
        "quantity": breakdown.quantity,
        "engine_version": breakdown.engine_version,
        "rate_snapshot_id": breakdown.rate_snapshot_id,
        "total": str(breakdown.total.amount),
        "unit_price": str(breakdown.unit_price.amount),
        "cost": str(breakdown.cost.amount),
        "margin": str(breakdown.margin.amount),
        "lines": [
            {
                "code": line.code,
                "category": line.category.value,
                "amount": str(line.amount.amount),
                "basis": {
                    "kind": line.basis.kind.value,
                    "quantity": _opt(line.basis.quantity),
                    "unit": line.basis.unit,
                    "rate": _opt(line.basis.rate),
                    "percent": _opt(line.basis.percent),
                    "of_codes": list(line.basis.of_codes),
                    "tier_min_quantity": line.basis.tier_min_quantity,
                },
            }
            for line in breakdown.lines
        ],
        "by_category": {
            category.value: str(amount.amount)
            for category, amount in breakdown.by_category().items()
        },
    }


def _render_delta(delta: BreakdownDelta) -> dict[str, Any]:
    return {
        "currency": delta.currency.value,
        "comparable": delta.comparable,
        "total_before": str(delta.total_before.amount),
        "total_after": str(delta.total_after.amount),
        "total_change": str(delta.total_change.amount),
        "unit_before": str(delta.unit_before.amount),
        "unit_after": str(delta.unit_after.amount),
        "unit_change": str(delta.unit_change.amount),
        "changed": [
            {
                "code": line.code,
                "category": line.category.value,
                "before": str(line.before.amount),
                "after": str(line.after.amount),
                "change": str(line.change.amount),
                "is_new": line.is_new,
                "is_removed": line.is_removed,
            }
            for line in delta.changed
        ],
    }


def _opt(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
