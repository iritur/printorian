"""The zone tariff as a settings row: parsed, stored, audited, resolved.

Its own file rather than more of `test_settings_catalogue.py`, which is 339 lines
against the 400-line gate.

The load-bearing test is the last one: the table has to reach
`RateSnapshot.zones`, because `logistics.zones` does not carry the `pricing.`
prefix that every other rate is matched by, and a settings row nothing resolves is
a number the owner set and the farm ignored.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.pricing import ShippingZone, ZoneTariffs
from printorian.contexts.settings import FIELDS, SECTIONS, Kind, SettingsService
from printorian.core.clock import FixedClock
from printorian.core.errors import ValidationError

ZONES = "logistics.zones"

MOSCOW = {
    "code": "msk",
    "base": "400",
    "per_kg": "0",
    "transit_days": 1,
    "postcode_prefixes": ["101", "1"],
    "enabled": True,
}
CENTRAL = {
    "code": "cfo",
    "base": "550",
    "per_kg": "60",
    "transit_days": 3,
    "postcode_prefixes": ["3"],
    "enabled": True,
}


def store(db: AsyncSession, clock: FixedClock) -> SettingsService:
    return SettingsService(db, clock)


# ------------------------------------------------------------ the catalogue


def test_the_zone_table_is_filed_under_logistics_with_its_own_panel() -> None:
    """The kit draws «Зоны и тарифы» as a panel, not a row among the packaging rates."""
    spec = FIELDS[ZONES]
    assert spec.section == "logistics"
    assert spec.kind is Kind.TABLE
    assert spec.group == "logistics.zones"
    assert ZONES in next(section for section in SECTIONS if section.id == "logistics").fields


def test_the_zone_table_is_the_third_table_valued_setting() -> None:
    """Two before it — the volume ladder and the customer tiers.

    Asserted by name rather than by count so that a fourth table added without a
    parser in `_parse_table` is a failing test rather than a 500 at the edge.
    """
    tables = [key for key, spec in FIELDS.items() if spec.kind is Kind.TABLE]
    assert tables == ["pricing.discounts", "pricing.tiers", ZONES]


def test_the_default_is_empty_so_a_farm_without_zones_ships_at_the_flat_rate() -> None:
    assert FIELDS[ZONES].default == []


# ------------------------------------------------------------ parsing


async def test_a_well_formed_table_round_trips(db_session: AsyncSession, clock: FixedClock) -> None:
    settings = store(db_session, clock)

    await settings.set_value(ZONES, [MOSCOW, CENTRAL], by=None)

    rows = {row.key: row for row in await settings.listing()}
    assert rows[ZONES].value == [MOSCOW, CENTRAL]
    assert rows[ZONES].is_overridden


async def test_something_that_is_not_a_list_is_refused_as_a_table(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(ZONES, {"code": "msk"}, by=None)
    assert raised.value.code == "error.settings.not_a_table"
    assert raised.value.details["key"] == ZONES


async def test_a_row_missing_its_base_is_refused_rather_than_read_as_free(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Defaulting the missing figure to zero would put free delivery in a quote."""
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(
            ZONES, [{"code": "msk", "postcode_prefixes": ["1"]}], by=None
        )
    assert raised.value.code == "error.settings.not_a_table"


async def test_a_negative_rate_surfaces_the_pricing_codes_own_refusal(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The screen owns shape errors; pricing owns pricing rules, and names the zone."""
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(ZONES, [{**CENTRAL, "per_kg": "-1"}], by=None)
    assert raised.value.code == "error.pricing.zone_negative_rate"
    assert raised.value.details["code"] == "cfo"


async def test_two_rows_with_one_code_are_refused(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(ZONES, [MOSCOW, {**MOSCOW}], by=None)
    assert raised.value.code == "error.pricing.duplicate_zone"


# ------------------------------------------------------------ the read edge


async def test_the_stored_table_reaches_the_resolved_rates(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """`logistics.zones` has no `pricing.` prefix, so an explicit mapping carries it.

    Without that mapping the owner edits a tariff and every quote keeps using the
    flat rate, silently.
    """
    settings = store(db_session, clock)

    await settings.set_value(ZONES, [MOSCOW, CENTRAL], by=None)

    resolved = await settings.resolve_rates()
    assert resolved.zones == ZoneTariffs(
        zones=(
            ShippingZone(
                code="msk",
                base=Decimal(400),
                per_kg=Decimal(0),
                transit_days=1,
                postcode_prefixes=("101", "1"),
            ),
            ShippingZone(
                code="cfo",
                base=Decimal(550),
                per_kg=Decimal(60),
                transit_days=3,
                postcode_prefixes=("3",),
            ),
        )
    )
    # One override must not rebuild the rest from nothing.
    assert resolved.shipping_flat == Decimal(400)
    assert resolved.margin_percent == Decimal(30)


async def test_no_stored_row_resolves_to_an_empty_table(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A key with no row is the code default — the rule the whole context rests on."""
    assert (await store(db_session, clock).resolve_rates()).zones == ZoneTariffs()


async def test_the_edit_is_audited_and_a_reset_returns_to_no_zones(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    settings = store(db_session, clock)
    await settings.set_value(ZONES, [MOSCOW], by=None)

    await settings.reset(ZONES, by=None)

    assert (await settings.resolve_rates()).zones == ZoneTariffs()
    changes = await settings.history(key=ZONES)
    assert [(change.old_value, change.new_value) for change in changes] == [
        ([MOSCOW], None),
        (None, [MOSCOW]),
    ]
