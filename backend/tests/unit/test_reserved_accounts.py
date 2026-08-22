"""Refusing to serve production from a developer's database.

The failure this prevents is specific and has a published exploit: `DEVELOPMENT.md`
lists two accounts with their passwords, one of them an owner, and restoring a dump
into another environment is described as routine in the backup runbook. Until this
guard existed, that combination gave anyone with the repository the owner password
on a farm seeded that way.

Both directions matter. A guard that refuses too eagerly is one somebody disables.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.api.app import _refuse_reserved_accounts
from printorian.contexts.identity import (
    CreateUser,
    IdentityService,
    Role,
    refusal_message,
    reserved_domain_accounts,
)
from printorian.contexts.identity.models import User
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus

PASSWORD = "correct-horse-battery"


async def make(
    db: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus, email: str
) -> None:
    await IdentityService(db, settings, clock, bus).create_user(
        CreateUser(email=email, display_name=email, password=PASSWORD, role=Role.CUSTOMER)
    )


# ------------------------------------------------------------ what it catches


@pytest.mark.parametrize(
    "email",
    [
        # The two the documentation publishes, and the domain they live in.
        "boss@printorian.example",
        "floor@printorian.example",
        # RFC 2606's three second-level domains, which the test suite itself uses.
        "someone@example.com",
        "someone@example.org",
        "someone@example.net",
    ],
)
async def test_a_documentation_address_is_reported(
    db_session: AsyncSession,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    email: str,
) -> None:
    await make(db_session, settings, clock, bus, email)

    assert await reserved_domain_accounts(db_session) == [email]


async def test_every_offending_account_is_named_not_counted(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """An operator told the farm will not start needs to know which rows to remove.

    A count sends them looking; the list is the difference between a two-minute
    fix and an outage somebody escalates.
    """
    for email in ("a@example.com", "b@printorian.example"):
        await make(db_session, settings, clock, bus, email)

    assert await reserved_domain_accounts(db_session) == [
        "a@example.com",
        "b@printorian.example",
    ]


async def test_the_refusal_names_the_accounts_and_how_to_clear_them(
    db_session: AsyncSession,
) -> None:
    message = refusal_message(["boss@printorian.example"])

    assert "boss@printorian.example" in message
    # Where to go next. This text is read by somebody whose farm will not start,
    # and a diagnosis with no route out of it is half a message.
    assert "RUNBOOK-FIRST-BOOT" in message
    # It used to print the DELETE itself, which was worse than it looked: nothing
    # referencing `users` restricts the delete, so a pasted one-liner always
    # succeeds and silently blanks the actor on every row it does not cascade
    # away. On a real database that is erasing history, so the statement now
    # lives behind the runbook section that explains what it costs.
    assert "DELETE" not in message


# ------------------------------------------------------------ what it must not catch


@pytest.mark.parametrize(
    "email",
    [
        "owner@thefarm.ru",
        "owner@printorian.com",
        # `.example` is only reserved as a *whole* label. A farm may legitimately
        # be called this, and refusing it would be the false positive that gets the
        # guard switched off.
        "owner@example-farm.ru",
        "owner@notexample.com",
        "example@thefarm.ru",
    ],
)
async def test_a_real_address_is_left_alone(
    db_session: AsyncSession,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    email: str,
) -> None:
    await make(db_session, settings, clock, bus, email)

    assert await reserved_domain_accounts(db_session) == []


async def test_an_empty_database_is_not_a_refusal(db_session: AsyncSession) -> None:
    """A fresh farm has no accounts at all, and must be allowed to start.

    It is the state every new deployment is in for the minutes between migrating
    and running `provision_owner.py`.
    """
    assert await reserved_domain_accounts(db_session) == []


async def test_the_check_is_case_insensitive(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Addresses are normalized on the way in, but a restored dump is not ours.

    A row that arrived from somewhere else may carry any case, and a guard that a
    capital letter defeats is not a guard.
    """
    # Built through the model rather than hand-written SQL: an INSERT listing
    # columns drifts the moment one is added, and this test would then fail for a
    # reason that has nothing to do with what it checks.
    db_session.add(
        User(
            email="BOSS@Printorian.EXAMPLE",
            display_name="restored",
            password_hash="x",
            role=Role.OWNER,
        )
    )
    await db_session.flush()

    assert await reserved_domain_accounts(db_session) == ["BOSS@Printorian.EXAMPLE"]


# ------------------------------------------------------------ the refusal at startup


class _StubDatabase:
    """`Database.session()` is an async generator; the guard consumes one."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def session(self) -> AsyncIterator[AsyncSession]:
        yield self._session


def _app_with(session: AsyncSession) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(database=_StubDatabase(session)))


async def test_startup_refuses_when_a_reserved_account_exists(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The whole point: not a warning in a log nobody reads, a process that stops.

    `RuntimeError` rather than a `PrintorianError` because this is not a request
    failing — it is the farm declining to exist in this configuration, and the
    error handlers that turn domain errors into responses must not be able to
    swallow it and carry on serving.
    """
    await make(db_session, settings, clock, bus, "boss@printorian.example")

    with pytest.raises(RuntimeError) as refusal:
        await _refuse_reserved_accounts(_app_with(db_session))

    assert "boss@printorian.example" in str(refusal.value)


async def test_startup_is_silent_on_a_real_database(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """A farm with real accounts starts, and so does one with none at all.

    The empty case is the one worth stating: it is what every new deployment looks
    like between migrating and provisioning the owner, and a guard that stopped
    there would stop the farm from ever being set up.
    """
    await _refuse_reserved_accounts(_app_with(db_session))

    await make(db_session, settings, clock, bus, "owner@thefarm.ru")
    await _refuse_reserved_accounts(_app_with(db_session))
