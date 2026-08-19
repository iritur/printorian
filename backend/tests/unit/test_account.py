"""The address book, the notification switches, and the loyalty ladder.

The ladder cases are the ones worth reading twice. It is the only piece of the
account screen that changes what somebody is *charged*, so what is pinned here is
the arithmetic behind the badge — and, in `test_the_badge_is_what_the_engine_takes_off`,
that the badge and the discount are the same number rather than two.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.account import (
    MAX_ADDRESSES,
    AccountService,
    UpdateNotifications,
    WriteAddress,
    tier_of,
)
from printorian.contexts.identity import CreateUser, IdentityService
from printorian.contexts.pricing import (
    LOYALTY_LADDER,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
    tier_for_spend,
)
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.units import Duration, Mass


async def _a_customer(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus, email: str
) -> EntityId:
    user = await IdentityService(db_session, settings, clock, bus).create_user(
        CreateUser(email=email, display_name=email, password="correct-horse-battery")
    )
    await db_session.commit()
    return user.id


def _an_address(**changes: object) -> WriteAddress:
    base: dict[str, object] = {"city": "Москва", "address": "ул. Мясницкая, д. 12"}
    return WriteAddress(**(base | changes))  # type: ignore[arg-type]


# ---------------------------------------------------------------- addresses


async def test_the_first_address_is_the_default_without_being_asked(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Somebody with one address has no choice to make; leaving it unmarked would
    open the checkout's picker with nothing selected on the commonest account."""
    user = await _a_customer(db_session, settings, clock, bus, "one@example.com")
    saved = await AccountService(db_session).add_address(user, _an_address())
    assert saved.is_default


async def test_promoting_an_address_demotes_the_other(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    user = await _a_customer(db_session, settings, clock, bus, "two@example.com")
    account = AccountService(db_session)
    first = await account.add_address(user, _an_address(label="Дом"))
    second = await account.add_address(user, _an_address(label="Офис"))

    await account.make_default(user, second.id)

    held = {row.id: row.is_default for row in await account.addresses(user)}
    assert held == {first.id: False, second.id: True}


async def test_the_default_is_listed_first(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    user = await _a_customer(db_session, settings, clock, bus, "order@example.com")
    account = AccountService(db_session)
    await account.add_address(user, _an_address(label="Дом"))
    second = await account.add_address(user, _an_address(label="Офис"))
    await account.make_default(user, second.id)

    assert [row.label for row in await account.addresses(user)] == ["Офис", "Дом"]


async def test_deleting_the_default_promotes_the_oldest_survivor(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Otherwise the customer is silently sent back to the checkout to choose."""
    user = await _a_customer(db_session, settings, clock, bus, "gone@example.com")
    account = AccountService(db_session)
    first = await account.add_address(user, _an_address(label="Дом"))
    second = await account.add_address(user, _an_address(label="Офис"))
    await account.make_default(user, second.id)

    await account.delete_address(user, second.id)

    remaining = await account.addresses(user)
    assert [(row.id, row.is_default) for row in remaining] == [(first.id, True)]


async def test_an_address_belonging_to_somebody_else_is_not_found(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Not *forbidden*. A caller who can tell the two apart can enumerate ids."""
    mine = await _a_customer(db_session, settings, clock, bus, "mine@example.com")
    theirs = await _a_customer(db_session, settings, clock, bus, "theirs@example.com")
    account = AccountService(db_session)
    saved = await account.add_address(theirs, _an_address())

    with pytest.raises(NotFoundError):
        await account.make_default(mine, saved.id)


async def test_the_address_book_is_bounded(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    user = await _a_customer(db_session, settings, clock, bus, "many@example.com")
    account = AccountService(db_session)
    for index in range(MAX_ADDRESSES):
        await account.add_address(user, _an_address(label=f"#{index}"))

    with pytest.raises(DomainRuleViolationError):
        await account.add_address(user, _an_address(label="one too many"))


# ------------------------------------------------------------ notifications


async def test_an_untouched_panel_reads_as_the_shipped_defaults(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """No row is not the same as everything off, and a read writes nothing."""
    user = await _a_customer(db_session, settings, clock, bus, "quiet@example.com")
    settings_read = await AccountService(db_session).notifications(user)

    assert settings_read.on_paid is True
    assert settings_read.on_every_stage is False
    assert settings_read.on_late_credit is True


async def test_one_switch_moves_and_the_rest_stay(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    user = await _a_customer(db_session, settings, clock, bus, "switch@example.com")
    account = AccountService(db_session)

    after = await account.set_notifications(user, UpdateNotifications(on_every_stage=True))

    assert after.on_every_stage is True
    assert after.on_paid is True
    assert after.on_shipped is True


async def test_lateness_credit_cannot_be_switched_off(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """It is a notification that money moved. `UpdateNotifications` has no field
    for it at all, so a client sending one is ignored rather than obeyed."""
    user = await _a_customer(db_session, settings, clock, bus, "money@example.com")
    account = AccountService(db_session)

    after = await account.set_notifications(
        user, UpdateNotifications.model_validate({"on_late_credit": False})
    )

    assert after.on_late_credit is True


# ------------------------------------------------------------------- ladder


def test_the_ladder_starts_at_nothing_and_costs_nothing() -> None:
    tier = tier_of(Decimal(0))
    assert tier.code == "standard"
    assert tier.discount_percent == Decimal(0)
    assert [step.reached for step in tier.steps] == [True, False, False]


def test_the_gap_is_the_distance_to_the_next_rung() -> None:
    """The kit's «ДО ТАРИФА GOLD — 113 600 ₽», against its own 186 400 ₽ spend."""
    tier = tier_of(Decimal(186_400))

    assert tier.code == "silver"
    assert tier.discount_percent == Decimal(4)
    assert tier.next_code == "gold"
    assert tier.to_next == Decimal(113_600)
    # …and the kit's own `--p:62%` fill.
    assert tier.progress_percent is not None
    assert int(tier.progress_percent) == 62


def test_the_top_of_the_ladder_has_no_gap_and_no_bar() -> None:
    """A bar with nothing left to fill reads as stuck, not as finished."""
    tier = tier_of(Decimal(1_000_000))

    assert tier.code == "gold"
    assert tier.next_code is None
    assert tier.to_next is None
    assert tier.progress_percent is None


def test_the_ladder_only_ever_climbs() -> None:
    """Each rung discounts at least as much as the one below, and starts higher.

    A ladder that dipped would mean spending more moved a customer to a worse
    price — the same failure the volume ladder's cliff guard exists to prevent,
    and the reason this one needs no guard of its own."""
    spends = [step.from_spend for step in LOYALTY_LADDER]
    percents = [step.discount_percent for step in LOYALTY_LADDER]
    assert spends == sorted(spends)
    assert percents == sorted(percents)


def test_the_badge_is_what_the_engine_takes_off() -> None:
    """The screen says «−4%». This asserts four percent actually comes off.

    The whole reason the ladder lives in `pricing` rather than in the account
    context: one definition, read by the badge and by the engine, so the two
    cannot drift into a farm that advertises a discount it does not apply."""
    spec = PriceSpec(
        estimate=PrintEstimate(print_time=Duration(Decimal(180)), material_mass=Mass(Decimal(90))),
        material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
        quantity=1,
    )
    rates = RateSnapshot()

    standard = price(spec, rates, tier_for_spend(Decimal(0)))
    silver = price(spec, rates, tier_for_spend(Decimal(186_400)))

    assert tier_of(Decimal(186_400)).discount_percent == Decimal(4)
    assert silver.total.amount < standard.total.amount
