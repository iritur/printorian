"""The table-valued settings: the volume ladder, the customer tiers, the finishes.

Split out of `test_settings_catalogue.py` rather than added to it. That file is the
*scalar* surface — a kind per field, a secret never read back, the audit — and it
was already at 339 lines against the 400-line gate. The seam is the one the next
table needs: everything here is a `Kind.TABLE` key, whose parser builds a domain
object and whose resolver hands it to a consumer, and that is a different job from
checking that a string round-trips.

The shape every test here follows on purpose: write JSON through `set_value` and
assert at the **resolver**, not at the store. A table that round-trips through the
column but never reaches `resolve_rates`/`resolve_tiers`/`resolve_finishes` is a
setting the farm can edit and the farm ignores, which is worse than one that is
missing, because the screen says it took.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.pricing import DiscountTier
from printorian.contexts.settings import SettingsService
from printorian.core.clock import FixedClock
from printorian.core.errors import ValidationError
from printorian.core.secrets import SecretBox


def store(db: AsyncSession, clock: FixedClock, *, secret: bool = False) -> SettingsService:
    box = SecretBox("a" * 32) if secret else None
    return SettingsService(db, clock, secret_box=box)


# ------------------------------------------------------------ the volume ladder


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


# ------------------------------------------------------------ the customer tiers


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


async def test_resolve_rates_ignores_a_pricing_setting_that_is_not_a_rate(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A table under `pricing.` must not be splatted into the rate snapshot.

    This failed before `resolve_rates` selected on `RateSnapshot`'s own field
    names: the old code took everything under the `pricing.` prefix, and
    `pricing.tiers` has no matching field, so the call raised `TypeError:
    RateSnapshot.__init__() got an unexpected keyword argument 'tiers'` — an
    uncoded 500 on every quote, order and reprice, from the moment an owner
    edited «Тарифы клиентов». Nothing caught it because the tiers test stopped at
    `resolve_tiers()` and the ladder test never set a tier; the two paths only
    collide when one session does both.
    """
    settings = store(db_session, clock)
    await settings.set_value(
        "pricing.tiers",
        [{"code": "standard", "discount_percent": "3", "margin_percent_override": None}],
        by=None,
    )

    rates = await settings.resolve_rates()

    # Unaffected by the tier edit, and specifically not a TypeError.
    assert rates.margin_percent == Decimal(30)
    # The rate that *is* a rate still lands, so the fix narrowed the selection
    # rather than emptying it.
    await settings.set_value("pricing.margin_percent", "42", by=None)
    assert (await settings.resolve_rates()).margin_percent == Decimal(42)
