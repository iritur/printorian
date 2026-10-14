"""API keys, against the real database: what one may be issued with, and what it becomes.

Issue #29's «Ключи API». The rules that make a key safe to paste into another
system (`identity.api_keys`):

* **only the key's hash is stored**, and the key is in the issuing response alone;
* **a key holds reads only**, and never more than its owner holds;
* **a key resolves to the owner's permissions *now***: demote the owner and it
  shrinks, deactivate them or revoke it and it stops.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.api.deps import requires
from printorian.contexts.identity import (
    KEY_PREFIX,
    Actor,
    ApiKeyService,
    CreateApiKey,
    CreateUser,
    IdentityService,
    Permission,
    Role,
    actor_of,
)
from printorian.contexts.identity.models import ApiKey, User
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import (
    DomainRuleViolationError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from printorian.core.events import EventBus
from printorian.core.ids import new_id


async def a_person(
    db: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus, role: Role
) -> Actor:
    view = await IdentityService(db, settings, clock, bus).create_user(
        CreateUser(
            email=f"{role.value}@example.com",
            display_name=role.value,
            password="correct-horse-battery",
            role=role,
        )
    )
    user = await db.get(User, view.id)
    assert user is not None
    return actor_of(user)


def floor_screen(*permissions: Permission) -> CreateApiKey:
    return CreateApiKey(
        name="Экран цеха", permissions=list(permissions or (Permission.VIEW_PRODUCTION,))
    )


async def test_the_key_is_shown_once_and_only_its_hash_is_kept(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    owner = await a_person(db_session, settings, clock, bus, Role.OWNER)
    keys = ApiKeyService(db_session, clock)

    issued = await keys.issue(owner, floor_screen())

    assert issued.secret.startswith(KEY_PREFIX)
    assert issued.key.prefix == issued.secret[:12]
    [row] = list(await db_session.scalars(select(ApiKey)))
    assert row.key_hash != issued.secret
    assert issued.secret not in row.key_hash
    # The listing carries the prefix and never the key or its hash.
    [listed] = await keys.listing()
    dumped = listed.model_dump_json()
    assert issued.secret not in dumped
    assert row.key_hash not in dumped
    assert listed.last_used_at is None


async def test_a_key_holds_reads_only_and_never_more_than_its_owner(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    owner = await a_person(db_session, settings, clock, bus, Role.OWNER)
    engineer = await a_person(db_session, settings, clock, bus, Role.ENGINEER)
    keys = ApiKeyService(db_session, clock)

    with pytest.raises(DomainRuleViolationError) as not_a_read:
        await keys.issue(owner, floor_screen(Permission.MANAGE_USERS))
    assert not_a_read.value.code == "error.identity.api_key_permission_not_allowed"

    # An engineer cannot see money, so cannot mint a key that does.
    with pytest.raises(PermissionDeniedError) as beyond:
        await keys.issue(engineer, floor_screen(Permission.VIEW_FINANCIALS))
    assert beyond.value.code == "error.identity.api_key_exceeds_owner"

    issued = await keys.issue(owner, floor_screen())
    as_key = await keys.resolve(issued.secret)
    # A key cannot mint keys, even through a path that forgot `CurrentActor`.
    with pytest.raises(PermissionDeniedError):
        await keys.issue(as_key, floor_screen())


async def test_a_key_resolves_to_what_its_owner_holds_now(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    owner = await a_person(db_session, settings, clock, bus, Role.OWNER)
    keys = ApiKeyService(db_session, clock)
    issued = await keys.issue(
        owner, floor_screen(Permission.VIEW_PRODUCTION, Permission.VIEW_FINANCIALS)
    )

    actor = await keys.resolve(issued.secret)
    assert actor.api_key_id == issued.key.id
    assert actor.user_id == owner.user_id
    assert actor.permissions == {Permission.VIEW_PRODUCTION, Permission.VIEW_FINANCIALS}

    # Demote the owner to engineer: the money half of the key goes with the role.
    user = await db_session.get(User, owner.user_id)
    assert user is not None
    user.role = Role.ENGINEER
    assert (await keys.resolve(issued.secret)).permissions == {Permission.VIEW_PRODUCTION}

    # Deactivate them, and the key stops.
    user.is_active = False
    with pytest.raises(UnauthenticatedError) as disabled:
        await keys.resolve(issued.secret)
    assert disabled.value.code == "error.identity.account_disabled"


async def test_a_revoked_key_stops_and_its_use_is_recorded_until_then(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    owner = await a_person(db_session, settings, clock, bus, Role.OWNER)
    keys = ApiKeyService(db_session, clock)
    issued = await keys.issue(owner, floor_screen())

    await keys.resolve(issued.secret)
    [listed] = await keys.listing()
    assert listed.last_used_at == clock.now()

    clock.advance(timedelta(hours=1))
    revoked = await keys.revoke(issued.key.id)
    assert revoked.revoked_at == clock.now()
    with pytest.raises(UnauthenticatedError) as stopped:
        await keys.resolve(issued.secret)
    assert stopped.value.code == "error.identity.invalid_api_key"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_requires_lets_a_key_read_and_never_write(method: str) -> None:
    """A write gated only by a read permission would otherwise be a write a
    leaked key could make; the method is what every such route shares."""
    guard = requires(Permission.VIEW_PRODUCTION)
    key = Actor(
        user_id=new_id(),
        email="owner@example.com",
        display_name="Экран цеха",
        role=Role.OWNER,
        locale="ru",
        permissions=frozenset({Permission.VIEW_PRODUCTION}),
        api_key_id=new_id(),
    )

    def request(verb: str) -> Request:
        return Request({"type": "http", "method": verb, "path": "/x", "headers": []})

    assert guard(request("GET"), key) is key
    with pytest.raises(PermissionDeniedError) as refused:
        guard(request(method), key)
    assert refused.value.code == "error.identity.api_key_read_only"
    # A person with the same permission is not affected.
    person = key.model_copy(update={"api_key_id": None})
    assert guard(request(method), person) is person
