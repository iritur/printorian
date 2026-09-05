"""The shipping zone table, and the one answer it must never invent.

The refusals matter because this table is edited by a person in a settings screen
and then priced against for real. The `zone_for` tests matter more: the whole
module exists so that an unmatched postcode is *not measured* rather than quietly
priced as Moscow (CLAUDE.md §1, ADR-0007).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from printorian.contexts.pricing import ShippingZone, ZoneTariffs, zone_for
from printorian.core.errors import ValidationError

MOSCOW = ShippingZone(
    code="msk",
    base=Decimal(400),
    per_kg=Decimal(0),
    transit_days=1,
    postcode_prefixes=("101", "1"),
)
CENTRAL = ShippingZone(
    code="cfo",
    base=Decimal(550),
    per_kg=Decimal(60),
    transit_days=3,
    postcode_prefixes=("3", "6"),
)


# ------------------------------------------------------------------ refusals


def test_a_negative_base_is_refused_with_the_offending_zone() -> None:
    with pytest.raises(ValidationError) as raised:
        ShippingZone(code="msk", base=Decimal(-1))
    assert raised.value.code == "error.pricing.zone_negative_rate"
    assert raised.value.details["code"] == "msk"
    assert raised.value.details["rate"] == "base"


def test_a_negative_per_kg_is_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        ShippingZone(code="cfo", per_kg=Decimal("-0.01"))
    assert raised.value.code == "error.pricing.zone_negative_rate"
    assert raised.value.details["rate"] == "per_kg"


def test_a_blank_prefix_is_refused_because_it_would_match_everything() -> None:
    with pytest.raises(ValidationError) as raised:
        ShippingZone(code="msk", postcode_prefixes=("101", " "))
    assert raised.value.code == "error.pricing.zone_prefix_empty"
    assert raised.value.details["code"] == "msk"


def test_a_blank_code_is_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        ShippingZone(code="  ")
    assert raised.value.code == "error.pricing.zone_code"


def test_negative_transit_days_are_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        ShippingZone(code="msk", transit_days=-1)
    assert raised.value.code == "error.pricing.zone_transit_days"


def test_two_rows_may_not_claim_the_same_code() -> None:
    with pytest.raises(ValidationError) as raised:
        ZoneTariffs(zones=(MOSCOW, ShippingZone(code="msk", base=Decimal(900))))
    assert raised.value.code == "error.pricing.duplicate_zone"
    assert raised.value.details["code"] == "msk"


# ------------------------------------------------------------------ matching


def test_the_longest_prefix_wins_when_two_zones_overlap() -> None:
    """«Москва» sits inside «Россия», and the narrow one is the right answer."""
    broad = ShippingZone(code="ru", base=Decimal(750), postcode_prefixes=("1",))
    tariffs = ZoneTariffs(zones=(broad, MOSCOW))
    assert zone_for(tariffs, "101000") is MOSCOW


def test_the_row_order_does_not_decide_the_zone() -> None:
    broad = ShippingZone(code="ru", base=Decimal(750), postcode_prefixes=("1",))
    forwards = ZoneTariffs(zones=(broad, MOSCOW))
    backwards = ZoneTariffs(zones=(MOSCOW, broad))
    assert zone_for(forwards, "101000") == zone_for(backwards, "101000")


def test_a_disabled_zone_is_skipped() -> None:
    """«Международная · выключена» in the kit: drawn, not served."""
    off = ShippingZone(code="intl", base=Decimal(4000), postcode_prefixes=("9",), enabled=False)
    assert zone_for(ZoneTariffs(zones=(off,)), "900001") is None


def test_a_postcode_matching_nothing_has_no_zone() -> None:
    """The guard this module exists for: no nearest zone, no first zone, None."""
    assert zone_for(ZoneTariffs(zones=(MOSCOW, CENTRAL)), "690000") is None


def test_an_empty_table_has_no_zone_for_any_postcode() -> None:
    assert zone_for(ZoneTariffs(), "101000") is None


def test_a_missing_postcode_has_no_zone_rather_than_a_default() -> None:
    """The pre-address checkout. Not knowing where it goes is not knowing."""
    assert zone_for(ZoneTariffs(zones=(MOSCOW,)), "") is None
    assert zone_for(ZoneTariffs(zones=(MOSCOW,)), "   ") is None


def test_surrounding_whitespace_and_case_do_not_change_the_answer() -> None:
    tariffs = ZoneTariffs(zones=(ShippingZone(code="uk", postcode_prefixes=("sw1",)),))
    assert zone_for(tariffs, " SW1A 1AA ") is not None
