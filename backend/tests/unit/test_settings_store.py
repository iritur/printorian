"""The settings store: defaults underneath, an audit beside, and no invented values.

The case the whole design turns on is the first one. A farm that upgrades into
this feature has an empty `settings` table, and every price it quotes the next
morning has to be the price it quoted the night before — otherwise the migration
that adds a settings screen is a migration that silently re-rates the business.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.pricing import RateSnapshot
from printorian.contexts.settings import SettingsService
from printorian.contexts.settings.models import Setting, SettingChange
from printorian.core.clock import FixedClock
from printorian.core.errors import NotFoundError, ValidationError

MARGIN = "pricing.margin_percent"
LABOUR = "pricing.labor_rate_per_hour"
CLIFFS = "pricing.guard_tier_cliffs"


def store(db: AsyncSession, clock: FixedClock) -> SettingsService:
    return SettingsService(db, clock)


# ------------------------------------------------------------ the default floor


async def test_an_empty_table_prices_exactly_as_the_code_does(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The upgrade case, and the reason nothing is seeded.

    Not "close to" the defaults — the same object. If this ever diverges, every
    farm's prices move on the morning it deploys.
    """
    assert await store(db_session, clock).resolve_rates() == RateSnapshot()


async def test_a_key_nobody_set_reports_its_default_and_says_it_is_unset(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    rows = {row.key: row for row in await store(db_session, clock).listing()}

    assert rows[MARGIN].value == str(RateSnapshot().margin_percent)
    assert rows[MARGIN].default == str(RateSnapshot().margin_percent)
    assert rows[MARGIN].is_overridden is False


async def test_every_scalar_rate_is_offered(db_session: AsyncSession, clock: FixedClock) -> None:
    """Derived from the dataclass, so a rate added later appears without upkeep.

    The converse is what makes it worth asserting: a hand-listed catalogue omits
    the next rate somebody adds, and a settings screen missing one looks complete.
    """
    offered = {row.key for row in await store(db_session, clock).listing()}

    assert MARGIN in offered
    assert LABOUR in offered
    # ...and the structured fields are *not* flattened into it: the discount ladder
    # is a table on the kit's screen, not a number in a box.
    assert not any(key.endswith("discounts") for key in offered)
    assert not any(key.endswith("currency") for key in offered)


# ------------------------------------------------------------ overriding


async def test_an_override_reaches_the_rates_a_quote_is_priced_with(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    settings = store(db_session, clock)

    await settings.set_value(MARGIN, "42", by=None)

    assert (await settings.resolve_rates()).margin_percent == Decimal(42)


async def test_the_other_rates_keep_their_defaults(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """One override must not rebuild the snapshot from nothing."""
    settings = store(db_session, clock)

    await settings.set_value(MARGIN, "42", by=None)

    resolved = await settings.resolve_rates()
    assert resolved.labor_rate_per_hour == RateSnapshot().labor_rate_per_hour
    assert resolved.currency == RateSnapshot().currency


async def test_a_decimal_survives_the_round_trip_exactly(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Stored as a string, for the reason `core.money` gives everywhere else.

    `6.50` through a JSON number is a float, and a float is not a price.
    """
    settings = store(db_session, clock)

    await settings.set_value("pricing.electricity_rate_per_kwh", "6.55", by=None)

    assert (await settings.resolve_rates()).electricity_rate_per_kwh == Decimal("6.55")


async def test_setting_it_twice_leaves_one_row(db_session: AsyncSession, clock: FixedClock) -> None:
    """The key is the identity. Two rows for one setting is a coin toss."""
    settings = store(db_session, clock)

    await settings.set_value(MARGIN, "40", by=None)
    await settings.set_value(MARGIN, "41", by=None)

    rows = list(await db_session.scalars(select(Setting).where(Setting.key == MARGIN)))
    assert len(rows) == 1
    assert (await settings.resolve_rates()).margin_percent == Decimal(41)


async def test_a_boolean_setting_round_trips(db_session: AsyncSession, clock: FixedClock) -> None:
    settings = store(db_session, clock)

    await settings.set_value(CLIFFS, False, by=None)

    assert (await settings.resolve_rates()).guard_tier_cliffs is False


# ------------------------------------------------------------ refusing nonsense


@pytest.mark.parametrize("bad", ["thirty", "", "30%", None, float("nan")])
async def test_a_value_that_is_not_a_number_is_refused(
    db_session: AsyncSession, clock: FixedClock, bad: object
) -> None:
    """Refused rather than coerced.

    A screen that turns `"30%"` into `30` has invented a number, and that number
    then reaches every quote the farm gives until somebody notices.
    """
    with pytest.raises(ValidationError):
        await store(db_session, clock).set_value(MARGIN, bad, by=None)


async def test_a_boolean_is_not_a_number(db_session: AsyncSession, clock: FixedClock) -> None:
    """`isinstance(True, int)` is True, which is how `margin_percent = 1` gets in."""
    with pytest.raises(ValidationError):
        await store(db_session, clock).set_value(MARGIN, True, by=None)


async def test_a_negative_rate_is_refused(db_session: AsyncSession, clock: FixedClock) -> None:
    """A negative margin is a farm quoting below cost until somebody notices."""
    with pytest.raises(ValidationError):
        await store(db_session, clock).set_value(MARGIN, "-5", by=None)


async def test_an_unknown_key_is_not_stored(db_session: AsyncSession, clock: FixedClock) -> None:
    """Otherwise the table fills with typos nothing will ever read."""
    with pytest.raises(NotFoundError):
        await store(db_session, clock).set_value("pricing.margin_pct", "40", by=None)


async def test_a_row_for_a_retired_key_does_not_break_resolution(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Settings outlive the code that reads them.

    A rate that is renamed leaves its row behind, and a farm that cannot price
    anything because of one is a farm held hostage by its own history.
    """
    db_session.add(
        Setting(key="pricing.retired_rate", value="1", updated_at=clock.now(), updated_by=None)
    )
    await db_session.flush()

    assert await store(db_session, clock).resolve_rates() == RateSnapshot()


# ------------------------------------------------------------ the audit


async def test_an_edit_records_what_it_was_and_what_it_became(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    settings = store(db_session, clock)

    await settings.set_value(MARGIN, "40", by=None)
    await settings.set_value(MARGIN, "45", by=None)

    history = await settings.history(key=MARGIN)
    assert [(row.old_value, row.new_value) for row in history] == [("40", "45"), (None, "40")]


async def test_the_first_edit_records_no_previous_value(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """«Было: nothing» is a different fact from «Было: the same number».

    The farm was on the code default, and the audit should not claim it had chosen
    that number deliberately.
    """
    settings = store(db_session, clock)

    await settings.set_value(MARGIN, "40", by=None)

    assert (await settings.history(key=MARGIN))[0].old_value is None


async def test_a_reset_returns_to_the_default_and_is_itself_audited(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """ "Back to the default" answers *why did the price change* too."""
    settings = store(db_session, clock)
    await settings.set_value(MARGIN, "40", by=None)

    view = await settings.reset(MARGIN, by=None)

    assert view.is_overridden is False
    assert (await settings.resolve_rates()).margin_percent == RateSnapshot().margin_percent
    assert (await settings.history(key=MARGIN))[0].new_value is None


async def test_the_audit_outlives_the_setting(db_session: AsyncSession, clock: FixedClock) -> None:
    """Resetting must not erase the period somebody is asking about."""
    settings = store(db_session, clock)
    await settings.set_value(MARGIN, "40", by=None)
    await settings.reset(MARGIN, by=None)

    assert await db_session.scalar(select(Setting).where(Setting.key == MARGIN)) is None
    assert len(await settings.history(key=MARGIN)) == 2


async def test_history_is_newest_first_and_can_be_read_for_one_key(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    settings = store(db_session, clock)
    await settings.set_value(MARGIN, "40", by=None)
    clock.advance(timedelta(minutes=1))
    await settings.set_value(LABOUR, "700", by=None)

    everything = await settings.history()
    assert [row.key for row in everything] == [LABOUR, MARGIN]
    assert [row.key for row in await settings.history(key=MARGIN)] == [MARGIN]


async def test_resetting_a_key_that_was_never_set_writes_nothing(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A reset with nothing to reset is not an edit, and should not look like one."""
    settings = store(db_session, clock)

    await settings.reset(MARGIN, by=None)

    assert list(await db_session.scalars(select(SettingChange))) == []
