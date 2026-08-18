"""Money invariants.

These exist because V1 mixed ``decimal`` and ``double`` through pricing and shipped
two calculators that disagreed. Every rule here is one that failure taught.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from printorian.core.errors import ValidationError
from printorian.core.money import Currency, Money, sum_money


def test_float_construction_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Money(19.99)  # type: ignore[arg-type]
    assert excinfo.value.code == "error.money.float_forbidden"


def test_decimal_str_and_int_are_accepted() -> None:
    assert Money("19.99").amount == Decimal("19.99")
    assert Money(20).amount == Decimal(20)
    assert Money(Decimal("0.1")).amount == Decimal("0.1")


def test_cross_currency_arithmetic_raises() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Money(10, Currency.RUB) + Money(10, Currency.USD)
    assert excinfo.value.code == "error.money.currency_mismatch"


def test_decimal_arithmetic_does_not_drift() -> None:
    total = sum_money([Money("0.1")] * 10)
    assert total.amount == Decimal("1.0")


def test_rounding_is_half_up() -> None:
    assert Money("2.345").rounded().amount == Decimal("2.35")
    assert Money("2.344").rounded().amount == Decimal("2.34")
    # Banker's rounding would give 2.34 here; costing must not round toward even.
    assert Money("2.335").rounded().amount == Decimal("2.34")


def test_allocate_conserves_every_minor_unit() -> None:
    parts = Money("100.00").allocate([1, 1, 1])
    assert [p.amount for p in parts] == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum_money(parts).amount == Decimal("100.00")


def test_allocate_respects_weights() -> None:
    parts = Money("10.00").allocate([3, 1])
    assert [p.amount for p in parts] == [Decimal("7.50"), Decimal("2.50")]


def test_allocate_conserves_for_negative_amounts() -> None:
    parts = Money("-100.00").allocate([1, 1, 1])
    assert sum_money(parts).amount == Decimal("-100.00")


def test_allocate_rejects_empty_and_nonpositive_weights() -> None:
    with pytest.raises(ValidationError):
        Money("1.00").allocate([])
    with pytest.raises(ValidationError):
        Money("1.00").allocate([0, 0])


def test_division_by_zero_raises() -> None:
    with pytest.raises(ValidationError):
        Money("1.00") / 0


def test_str_is_machine_readable_not_localized() -> None:
    # ADR-0012: the backend never formats money for humans.
    assert str(Money("1234.5", Currency.RUB)) == "1234.50 RUB"
