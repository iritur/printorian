"""Turning a :class:`Breakdown` into plain data, and back.

There is exactly one implementation, used by both the API response and the copy
pinned onto an order. A second serializer would be the first step back toward V1,
where the wire format and the stored format drifted apart until nobody could
reproduce an old quote.

Round-tripping matters: an order stores its breakdown verbatim, so years later the
exact figures a customer agreed to can be read back and rendered again, without
re-running the engine under whatever rules apply by then.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import Any

from printorian.contexts.pricing.breakdown import (
    Basis,
    BasisKind,
    Breakdown,
    BreakdownDelta,
    Category,
    LineItem,
)
from printorian.contexts.pricing.rates import DiscountLadder, DiscountTier, RateSnapshot
from printorian.core.money import Currency, Money

SCHEMA_VERSION = 1


def basis_to_dict(basis: Basis) -> dict[str, Any]:
    return {
        "kind": basis.kind.value,
        "quantity": _num(basis.quantity),
        "unit": basis.unit,
        "rate": _num(basis.rate),
        "percent": _num(basis.percent),
        "of_codes": list(basis.of_codes),
        "tier_min_quantity": basis.tier_min_quantity,
    }


def basis_from_dict(data: dict[str, Any]) -> Basis:
    return Basis(
        kind=BasisKind(data["kind"]),
        quantity=_dec(data.get("quantity")),
        unit=data.get("unit"),
        rate=_dec(data.get("rate")),
        percent=_dec(data.get("percent")),
        of_codes=tuple(data.get("of_codes") or ()),
        tier_min_quantity=data.get("tier_min_quantity"),
    )


def line_to_dict(line: LineItem) -> dict[str, Any]:
    return {
        "code": line.code,
        "category": line.category.value,
        "amount": str(line.amount.amount),
        "basis": basis_to_dict(line.basis),
    }


def line_from_dict(data: dict[str, Any], currency: Currency) -> LineItem:
    return LineItem(
        code=data["code"],
        category=Category(data["category"]),
        amount=Money(data["amount"], currency),
        basis=basis_from_dict(data["basis"]),
    )


def breakdown_to_dict(breakdown: Breakdown) -> dict[str, Any]:
    """Serialize a priced breakdown.

    Totals are included even though they are derivable, so a stored quote can be
    displayed without reconstructing anything — and so a mismatch between the sum
    and the recorded total is detectable rather than silently papered over.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "currency": breakdown.currency.value,
        "quantity": breakdown.quantity,
        "engine_version": breakdown.engine_version,
        "rate_snapshot_id": breakdown.rate_snapshot_id,
        "total": str(breakdown.total.amount),
        "unit_price": str(breakdown.unit_price.amount),
        "cost": str(breakdown.cost.amount),
        "margin": str(breakdown.margin.amount),
        "lines": [line_to_dict(line) for line in breakdown.lines],
        "by_category": {
            category.value: str(amount.amount)
            for category, amount in breakdown.by_category().items()
        },
    }


def breakdown_from_dict(data: dict[str, Any]) -> Breakdown:
    """Rebuild a breakdown from stored data.

    Derived totals are recomputed from the lines rather than trusted, so a
    corrupted or hand-edited record surfaces instead of being believed.
    """
    currency = Currency(data["currency"])
    return Breakdown(
        lines=tuple(line_from_dict(line, currency) for line in data["lines"]),
        currency=currency,
        quantity=int(data["quantity"]),
        engine_version=data["engine_version"],
        rate_snapshot_id=data["rate_snapshot_id"],
    )


def delta_to_dict(delta: BreakdownDelta) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "currency": delta.currency.value,
        "comparable": delta.comparable,
        "total_before": str(delta.total_before.amount),
        "total_after": str(delta.total_after.amount),
        "total_change": str(delta.total_change.amount),
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


# ------------------------------------------------------------------ rates


def rates_to_dict(rates: RateSnapshot) -> dict[str, Any]:
    """Serialize the rates a quote was built from.

    Written over ``dataclasses.fields`` rather than a hand-listed set of keys, and
    that is the whole point: a hand-listed serializer silently omits the next rate
    somebody adds, and a snapshot missing a rate is worse than no snapshot at all —
    it looks complete and reproduces a different number. Adding a field to
    :class:`RateSnapshot` is picked up here automatically, and
    ``test_rates_round_trip`` fails loudly if its *type* is one this cannot carry.

    ``snapshot_id`` is stored alongside the values so a stored row can be checked
    against its own key without reconstructing the object.
    """
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": rates.snapshot_id,
    }
    for field in fields(rates):
        payload[field.name] = _rate_to_json(getattr(rates, field.name))
    return payload


def rates_from_dict(data: dict[str, Any]) -> RateSnapshot:
    """Rebuild the rates from a stored row.

    The reverse of :func:`rates_to_dict`, and the half that makes ADR-0002 true:
    without it a stored snapshot is an archive nobody can run the engine against.
    """
    kwargs: dict[str, Any] = {}
    for field in fields(RateSnapshot):
        if field.name not in data:
            continue
        kwargs[field.name] = _rate_from_json(field.name, data[field.name])
    return RateSnapshot(**kwargs)


def _rate_to_json(value: Any) -> Any:
    if isinstance(value, DiscountLadder):
        return [
            {"min_quantity": tier.min_quantity, "percent": str(tier.percent)}
            for tier in value.tiers
        ]
    if isinstance(value, Currency):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool | int | str) or value is None:
        return value
    raise TypeError(f"rate value of unsupported type {type(value)!r} cannot be serialized")


def _rate_from_json(name: str, value: Any) -> Any:
    if name == "discounts":
        return DiscountLadder(
            tiers=tuple(
                DiscountTier(
                    min_quantity=int(tier["min_quantity"]),
                    percent=Decimal(tier["percent"]),
                )
                for tier in value
            )
        )
    if name == "currency":
        return Currency(value)
    if isinstance(value, bool) or value is None:
        return value
    return Decimal(value)


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


#: Short aliases for use inside the pricing package.
to_dict = breakdown_to_dict
from_dict = breakdown_from_dict
