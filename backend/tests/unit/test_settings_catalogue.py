"""The extended catalogue: fourteen editable sections, typed fields, and write-only secrets.

The pricing-only behaviour is covered by `test_settings_store.py`; this file is
the new surface — that every kit section is represented, that each field knows its
own type, and that a secret is never read back and never appears in the audit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import PromisePolicy
from printorian.contexts.pricing import DiscountTier
from printorian.contexts.scheduling import SchedulingPolicy
from printorian.contexts.settings import FIELDS, SECTIONS, Kind, SettingsService
from printorian.contexts.settings.models import Setting
from printorian.core.clock import FixedClock
from printorian.core.errors import ConfigurationError, ValidationError
from printorian.core.secrets import SecretBox

FARM_NAME = "general.farm_name"
LOW_STOCK = "inventory.low_stock_grams"
TAX_REGIME = "finance.tax_regime"
SECRET_KEY = "finance.yookassa_secret_key"


def store(db: AsyncSession, clock: FixedClock, *, secret: bool = False) -> SettingsService:
    box = SecretBox("a" * 32) if secret else None
    return SettingsService(db, clock, secret_box=box)


# ------------------------------------------------------------ the sections


async def test_the_rail_has_every_editable_section_in_kit_order(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Diagnostics is read-only, so it is the one heading with no fields here."""
    ids = [section.id for section in SECTIONS]

    assert ids == [
        "general",
        "pricing",
        "discounts",
        "scheduling",
        "sla",
        "inventory",
        "service",
        "postprocess",
        "logistics",
        "finance",
        "notify",
        "security",
        "integrations",
        "maintenance",
    ]


async def test_no_section_repeats_a_group_heading(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Each panel heading appears once per section, because the screen draws a
    panel per *run* of consecutive fields sharing a group.

    A split group therefore drew its heading twice, one panel a single field
    wide, and gave two React siblings the same key. It happened to two sections:
    the pricing fields come off `RateSnapshot`'s declaration order and the
    weights off `SchedulingPolicy`'s, so neither list is arranged by panel.
    `groups.in_group_order` is what keeps the runs whole.
    """
    for section in SECTIONS:
        runs: list[str | None] = []
        for key in section.fields:
            group = FIELDS[key].group
            if not runs or runs[-1] != group:
                runs.append(group)

        assert len(runs) == len(set(runs)), f"section {section.id!r} draws a heading twice: {runs}"


async def test_grouping_keeps_the_kits_field_order_within_a_panel(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Grouping reorders panels, never the fields inside one — «Труд» still
    reads rate, hours-per-print-hour, hours-per-job, as the kit lists it."""
    pricing = {section.id: section for section in SECTIONS}["pricing"]
    labor = [key for key in pricing.fields if FIELDS[key].group == "pricing.labor"]

    assert labor[:3] == [
        "pricing.labor_rate_per_hour",
        "pricing.labor_hours_per_print_hour",
        "pricing.labor_hours_per_job",
    ]
    # The field that was stranded at the end of the section, now beside its own.
    material = [key for key in pricing.fields if FIELDS[key].group == "pricing.material"]
    assert "pricing.multicolor_purge_grams_per_extra_color" in material


async def test_sections_group_by_the_kits_headings_not_the_key_prefix(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A rate shown under «Логистика» is still a `pricing.*` key — the screen's
    heading and the storage namespace are different facts."""
    by_section = {section.id: set(section.fields) for section in SECTIONS}

    assert "pricing.packaging_per_unit" in by_section["logistics"]
    assert "pricing.shipping_flat" in by_section["logistics"]
    assert "pricing.guard_tier_cliffs" in by_section["discounts"]
    assert "pricing.margin_percent" in by_section["pricing"]


async def test_sections_returns_the_grouped_view_in_order(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The endpoint the screen reads is built from the same catalogue, not a second list."""
    sections = await store(db_session, clock).sections()

    assert [section.id for section in sections] == [section.id for section in SECTIONS]
    by_id = {section.id: section for section in sections}
    logistics_keys = {field.key for field in by_id["logistics"].fields}
    assert "pricing.packaging_per_unit" in logistics_keys
    assert "logistics.volumetric_divisor" in logistics_keys
    # Every field carries its own kind, so the screen's control is data-driven.
    assert all(field.kind for field in by_id["finance"].fields)


async def test_a_promise_setting_changes_the_resolved_policy(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The lead-time settings reach the read edge, not just the table."""
    settings = store(db_session, clock)

    assert await settings.resolve_promise() == PromisePolicy()

    await settings.set_value("sla.min_lead_hours", "48", by=None)

    resolved = await settings.resolve_promise()
    assert resolved.min_lead_hours == Decimal(48)
    # The others keep their defaults: one override must not rebuild from nothing.
    assert resolved.promise_buffer_percent == PromisePolicy().promise_buffer_percent
    assert resolved.rush_lead_hours == PromisePolicy().rush_lead_hours


async def test_a_scheduler_weight_changes_the_resolved_policy(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The planner weights reach the read edge, not just the table."""
    settings = store(db_session, clock)

    assert await settings.resolve_scheduling() == SchedulingPolicy()

    await settings.set_value("scheduling.weight_load_balance", "9", by=None)

    resolved = await settings.resolve_scheduling()
    assert resolved.weight_load_balance == Decimal(9)
    # The other weights keep their defaults: one override must not rebuild from nothing.
    assert resolved.due_soon_hours == SchedulingPolicy().due_soon_hours
    assert resolved.weight_capability_waste == SchedulingPolicy().weight_capability_waste


async def test_a_ladder_round_trips_and_reaches_the_rates(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The volume ladder is a table, not a scalar, and it reaches `resolve_rates`."""
    settings = store(db_session, clock)
    ladder = [
        {"min_quantity": 10, "percent": "5"},
        {"min_quantity": 50, "percent": "12"},
    ]

    await settings.set_value("pricing.discounts", ladder, by=None)

    resolved = await settings.resolve_rates()
    assert resolved.discounts.tiers == (
        DiscountTier(min_quantity=10, percent=Decimal(5)),
        DiscountTier(min_quantity=50, percent=Decimal(12)),
    )
    rows = {row.key: row for row in await settings.listing()}
    assert rows["pricing.discounts"].value == ladder


async def test_an_inverting_ladder_is_refused(db_session: AsyncSession, clock: FixedClock) -> None:
    """A ladder that undercuts itself is refused with the pricing engine's own code."""
    with pytest.raises(ValidationError):
        await store(db_session, clock).set_value(
            "pricing.discounts",
            [
                {"min_quantity": 10, "percent": "12"},
                {"min_quantity": 50, "percent": "5"},
            ],
            by=None,
        )


async def test_reset_prefix_drops_every_override_and_audits_each(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """«Сбросить тарифы» deletes only the pricing rows, and each is audited."""
    settings = store(db_session, clock)
    await settings.set_value("pricing.margin_percent", "42", by=None)
    await settings.set_value("pricing.labor_rate_per_hour", "700", by=None)
    await settings.set_value("general.farm_name", "X", by=None)

    count = await settings.reset_prefix("pricing.", by=None)

    assert count == 2
    assert (await settings.resolve_rates()).margin_percent == Decimal(30)
    rows = {row.key: row for row in await settings.listing()}
    assert rows["general.farm_name"].is_overridden is True
    assert rows["pricing.margin_percent"].is_overridden is False
    reset_keys = [row.key for row in await settings.history() if row.new_value is None]
    assert set(reset_keys) == {"pricing.margin_percent", "pricing.labor_rate_per_hour"}


async def test_tiers_resolve_with_defaults_and_overrides(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The customer tiers default from the loyalty ladder, and overrides land."""
    settings = store(db_session, clock)

    defaults = await settings.resolve_tiers()
    assert defaults["silver"].discount_percent == Decimal(4)
    assert defaults["gold"].margin_percent_override is None

    await settings.set_value(
        "pricing.tiers",
        [
            {"code": "standard", "discount_percent": "0", "margin_percent_override": None},
            {"code": "silver", "discount_percent": "10", "margin_percent_override": None},
            {"code": "gold", "discount_percent": "8", "margin_percent_override": "22"},
        ],
        by=None,
    )

    resolved = await settings.resolve_tiers()
    assert resolved["silver"].discount_percent == Decimal(10)
    assert resolved["gold"].margin_percent_override == Decimal(22)


async def test_a_discount_at_or_past_100_percent_is_refused(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A tier discount that reaches 100% is a negative price — never intended."""
    with pytest.raises(ValidationError):
        await store(db_session, clock).set_value(
            "pricing.tiers",
            [{"code": "standard", "discount_percent": "100", "margin_percent_override": None}],
            by=None,
        )


async def test_every_field_knows_its_kind(db_session: AsyncSession, clock: FixedClock) -> None:
    """The screen cannot draw the right control from a type the catalogue forgot."""
    assert FIELDS[FARM_NAME].kind is Kind.STRING
    assert FIELDS[LOW_STOCK].kind is Kind.INTEGER
    assert FIELDS["pricing.margin_percent"].kind is Kind.DECIMAL
    assert FIELDS[TAX_REGIME].kind is Kind.ENUM
    assert FIELDS["general.unattended_printing"].kind is Kind.BOOLEAN
    assert FIELDS[SECRET_KEY].kind is Kind.SECRET


# ------------------------------------------------------------ new kinds


async def test_a_string_round_trips(db_session: AsyncSession, clock: FixedClock) -> None:
    settings = store(db_session, clock)

    view = await settings.set_value(FARM_NAME, "KN-SOL.42", by=None)

    assert view.value == "KN-SOL.42"
    rows = {row.key: row for row in await settings.listing()}
    assert rows[FARM_NAME].value == "KN-SOL.42"


async def test_an_integer_refuses_a_bool(db_session: AsyncSession, clock: FixedClock) -> None:
    """`isinstance(True, int)` is True — a stock threshold of `True` is not 1 gram."""
    with pytest.raises(ValidationError):
        await store(db_session, clock).set_value(LOW_STOCK, True, by=None)


async def test_an_enum_round_trips_and_refuses_an_unknown_option(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    settings = store(db_session, clock)

    assert (await settings.set_value(TAX_REGIME, "npd", by=None)).value == "npd"

    with pytest.raises(ValidationError):
        await settings.set_value(TAX_REGIME, "vat_farming", by=None)


# ------------------------------------------------------------ secrets


async def test_a_secret_is_never_read_back(db_session: AsyncSession, clock: FixedClock) -> None:
    settings = store(db_session, clock, secret=True)

    await settings.set_value(SECRET_KEY, "sk_live_123", by=None)

    rows = {row.key: row for row in await settings.listing()}
    assert rows[SECRET_KEY].value is None
    assert rows[SECRET_KEY].is_set is True


async def test_a_secret_is_encrypted_at_rest(db_session: AsyncSession, clock: FixedClock) -> None:
    settings = store(db_session, clock, secret=True)
    await settings.set_value(SECRET_KEY, "sk_live_123", by=None)

    stored = await db_session.scalar(select(Setting).where(Setting.key == SECRET_KEY))

    assert stored is not None
    assert stored.value != "sk_live_123"
    assert stored.value.startswith("enc:v1:")


async def test_a_secret_never_appears_in_the_audit(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The audit answers *who* and *when*, never *what the key was*."""
    settings = store(db_session, clock, secret=True)
    await settings.set_value(SECRET_KEY, "sk_live_123", by=None)
    await settings.set_value(SECRET_KEY, "sk_live_999", by=None)

    history = await settings.history(key=SECRET_KEY)

    assert len(history) == 2
    assert all(row.old_value is None and row.new_value is None for row in history)


async def test_a_secret_without_a_box_cannot_be_written(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """No key material, no secret — the value must not fall back to plaintext."""
    with pytest.raises(ConfigurationError):
        await store(db_session, clock).set_value(SECRET_KEY, "sk_live_123", by=None)
