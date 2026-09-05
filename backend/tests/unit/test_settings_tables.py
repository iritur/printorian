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

What this file deliberately does *not* prove is that a resolved value reaches a
price. Every test here passes with the routers still reading `FINISH_CATALOGUE`,
so `tests/api/test_finish_catalogue.py` drives the real endpoints and asserts the
money moved. Both are needed: this one says the parser refuses what it should,
that one says the refusals are guarding something.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.pricing import FINISH_CATALOGUE, DiscountTier
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


# ------------------------------------------------------ the postprocess catalogue


def a_catalogue(**edits: dict[str, str]) -> list[dict[str, object]]:
    """The four finishes as JSON, with named rows edited.

    Built from `FINISH_CATALOGUE` rather than typed out, so a test asserting that
    an *unknown* code is refused cannot pass merely because the author mistyped a
    known one somewhere else in the list.
    """
    rows: list[dict[str, object]] = []
    for code, finish in FINISH_CATALOGUE.items():
        row: dict[str, object] = {
            "code": code,
            "labor_hours": str(finish.labor_hours),
            "flat_fee": str(finish.flat_fee),
            "extra_days": finish.extra_days,
        }
        row.update(edits.get(code, {}))
        rows.append(row)
    return rows


async def test_a_finish_catalogue_round_trips_and_reaches_resolve_finishes(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The farm's norm-hours land on the resolver, and the store holds strings."""
    settings = store(db_session, clock)

    # The default is what the farm is running today, not the design kit's figures:
    # `design/settings.html` draws sanding and painting at different numbers, and
    # taking the kit's would have repriced every quote on the day this merged.
    assert (await settings.resolve_finishes())["sanded"].labor_hours == Decimal("0.4")

    await settings.set_value(
        "postprocess.operations", a_catalogue(sanded={"labor_hours": "0.9"}), by=None
    )

    resolved = await settings.resolve_finishes()
    assert resolved["sanded"].labor_hours == Decimal("0.9")
    # Carried through untouched even though no editor draws it: `extra_days` feeds
    # the SLA promise, and a lossy round trip would shorten what «Окраска» promises
    # the first time somebody edited a norm-hour.
    assert resolved["painted"].extra_days == 2

    rows = {row.key: row for row in await settings.listing()}
    stored = rows["postprocess.operations"].value
    assert stored[1] == {
        "code": "sanded",
        "labor_hours": "0.9",
        "flat_fee": "0",
        "extra_days": 0,
    }
    # Strings in the column and on the wire. A JSON number is a float, and a float
    # is not a price.
    assert all(isinstance(row["flat_fee"], str) for row in stored)


async def test_a_finish_catalogue_missing_a_code_is_refused(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Dropping a row would leave the storefront offering a finish nothing prices."""
    rows = [row for row in a_catalogue() if row["code"] != "primed"]

    with pytest.raises(ValidationError) as excinfo:
        await store(db_session, clock).set_value("postprocess.operations", rows, by=None)

    assert excinfo.value.code == "error.settings.finish_code_missing"
    # Structured detail rather than a sentence (ADR-0012) — the console
    # interpolates the codes into its own message.
    assert excinfo.value.details["codes"] == ["primed"]


async def test_a_finish_catalogue_with_an_unknown_code_is_refused(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """A fifth operation is a row the storefront would never offer.

    `apps/web/src/config.ts` hardcodes the four codes and `_build_spec` refuses
    anything else at the quote, so adding one here would be a finish the farm had
    priced, the console had shown, and no customer could ever choose. Widening the
    set is a feature — a public read of the catalogue — not a check to relax.
    """
    rows = [*a_catalogue(), {"code": "polished", "labor_hours": "2", "flat_fee": "500"}]

    with pytest.raises(ValidationError) as excinfo:
        await store(db_session, clock).set_value("postprocess.operations", rows, by=None)

    assert excinfo.value.code == "error.settings.finish_code_unknown"
    assert excinfo.value.details["codes"] == ["polished"]


async def test_a_duplicate_finish_code_is_refused(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Two rows for one code do not merge — the later would silently win.

    `resolve_finishes` keys by code, so a duplicate means the row an owner edited
    and the row the farm charges from can differ by where they sat in the list.
    """
    rows = [*a_catalogue(), {"code": "sanded", "labor_hours": "5", "flat_fee": "0"}]

    with pytest.raises(ValidationError) as excinfo:
        await store(db_session, clock).set_value("postprocess.operations", rows, by=None)

    assert excinfo.value.code == "error.settings.finish_code_duplicate"
    assert excinfo.value.details["codes"] == ["sanded"]


async def test_a_negative_finish_fee_is_refused_by_the_engines_own_code(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The parser builds the real domain object, so the domain's rule applies.

    The assertion is on `error.pricing.*` and not `error.settings.*` on purpose: a
    settings-layer code here would mean `_parse_finishes` had built dicts and
    written a second copy of a rule `FinishOption.__post_init__` already carries.
    """
    with pytest.raises(ValidationError) as excinfo:
        await store(db_session, clock).set_value(
            "postprocess.operations", a_catalogue(primed={"flat_fee": "-1"}), by=None
        )

    assert excinfo.value.code == "error.pricing.finish_negative"


async def test_resetting_the_rates_does_not_clear_the_finish_catalogue(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """«Сбросить тарифы» is the rate book, not the finishing station.

    This pins the naming decision rather than the plumbing. `reset_prefix` deletes
    on `pricing.`, so had the catalogue been called `pricing.finishes` an owner
    resetting their margins would also have thrown away the norm-hours their
    finishing station is measured against — one of the three irreversible actions
    the screen offers, and the one most in need of a test.
    """
    settings = store(db_session, clock)
    await settings.set_value("pricing.margin_percent", "42", by=None)
    await settings.set_value(
        "postprocess.operations", a_catalogue(sanded={"labor_hours": "0.9"}), by=None
    )

    removed = await settings.reset_prefix("pricing.", by=None)

    assert removed == 1
    assert (await settings.resolve_rates()).margin_percent == Decimal(30)
    assert (await settings.resolve_finishes())["sanded"].labor_hours == Decimal("0.9")
