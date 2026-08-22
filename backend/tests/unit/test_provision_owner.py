"""Creating the farm's first owner.

Every other account is created by somebody who already has one. This is the single
exception, it runs exactly once per deployment, and it runs on a farm where nobody
can sign in yet — so a bug in it has no working state to be compared against and no
owner to ask. That is the argument for testing a hundred-line script this closely.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity import Role
from printorian.contexts.identity.models import User
from printorian.core.config import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.provision_owner import provision_with

PASSWORD = "first-owner-password"


def _password() -> str:
    return PASSWORD


async def test_an_empty_farm_gets_its_owner(db_session: AsyncSession, settings: Settings) -> None:
    """The path that runs on first boot, and has to work the first time."""
    code = await provision_with(db_session, settings, "owner@thefarm.ru", "Owner", _password)

    assert code == 0
    created = (await db_session.scalars(select(User))).one()
    assert created.email == "owner@thefarm.ru"
    assert created.role is Role.OWNER
    assert created.is_active


async def test_the_password_is_hashed_not_stored(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Belt and braces over `IdentityService`, because this is *the* account.

    If the one account that can change every rate the farm charges were ever stored
    in plaintext, this is the test that should have failed.
    """
    await provision_with(db_session, settings, "owner@thefarm.ru", "Owner", _password)

    created = (await db_session.scalars(select(User))).one()
    assert PASSWORD not in created.password_hash
    assert created.password_hash.startswith("$argon2")


async def test_a_second_run_refuses_and_changes_nothing(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Provisioning is first boot, not a password reset.

    The two want opposite behaviour — a reset should be repeatable, this should be
    impossible to repeat by accident — and the failure being prevented is a silent
    second owner nobody knows about.
    """
    await provision_with(db_session, settings, "owner@thefarm.ru", "Owner", _password)
    before = (await db_session.scalars(select(User))).one()

    code = await provision_with(db_session, settings, "intruder@thefarm.ru", "Intruder", _password)

    assert code == 1
    after = (await db_session.scalars(select(User))).one()
    assert after.id == before.id
    assert after.password_hash == before.password_hash


async def test_the_refusal_does_not_ask_for_a_password(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Order matters: check first, prompt second.

    Prompting before checking would have the operator type the farm's most
    sensitive password into a terminal for a run that was always going to refuse.
    """
    await provision_with(db_session, settings, "owner@thefarm.ru", "Owner", _password)

    def _explode() -> str:
        raise AssertionError("asked for a password on a run that refuses")

    assert await provision_with(db_session, settings, "x@thefarm.ru", "X", _explode) == 1


async def test_an_existing_non_owner_does_not_block_provisioning(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Only an *owner* is the thing that already exists.

    A restore that carried customers across still needs its owner created, and a
    check on "any user at all" would leave that farm with no way in.
    """
    db_session.add(
        User(
            email="customer@thefarm.ru",
            display_name="Customer",
            password_hash="x",
            role=Role.CUSTOMER,
        )
    )
    await db_session.flush()

    code = await provision_with(db_session, settings, "owner@thefarm.ru", "Owner", _password)

    assert code == 0
    owners = (await db_session.scalars(select(User).where(User.role == Role.OWNER))).all()
    assert [owner.email for owner in owners] == ["owner@thefarm.ru"]


@pytest.mark.parametrize("bad", ["", "short", "123456789"])
async def test_a_password_the_farm_would_reject_is_refused_at_the_terminal(
    db_session: AsyncSession, settings: Settings, bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caught while the operator is still standing there, not after a stack trace."""
    import tools.provision_owner as script

    monkeypatch.setattr(script.getpass, "getpass", lambda _prompt="": bad)

    with pytest.raises(SystemExit):
        script._read_password()
