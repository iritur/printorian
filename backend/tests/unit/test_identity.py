"""Identity service behaviour: registration, sign-in, sessions."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity import (
    MIN_PASSWORD_LENGTH,
    CreateUser,
    IdentityService,
    Permission,
    Role,
    SignIn,
)
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.errors import ConflictError, UnauthenticatedError
from printorian.core.events import EventBus

PASSWORD = "correct-horse-battery"


@pytest.fixture
def service(
    db_session: AsyncSession, settings: Settings, clock: FixedClock, bus: EventBus
) -> IdentityService:
    return IdentityService(db_session, settings, clock, bus)


async def _register(service: IdentityService, email: str = "a@example.com", **kw: object) -> object:
    return await service.create_user(
        CreateUser(email=email, display_name="Test", password=PASSWORD, **kw)  # type: ignore[arg-type]
    )


def test_min_password_length_matches_the_schema() -> None:
    # The service constant and the DTO constraint must not drift apart.
    assert CreateUser.model_fields["password"].metadata[0].min_length == MIN_PASSWORD_LENGTH


async def test_registration_normalizes_email_and_defaults_to_customer(
    service: IdentityService,
) -> None:
    user = await _register(service, email="  Owner@Example.COM ")
    assert user.email == "owner@example.com"  # type: ignore[attr-defined]
    assert user.role is Role.CUSTOMER  # type: ignore[attr-defined]


async def test_duplicate_email_is_rejected_case_insensitively(service: IdentityService) -> None:
    await _register(service, email="dup@example.com")
    with pytest.raises(ConflictError):
        await _register(service, email="DUP@example.com")


async def test_password_is_never_stored_in_clear(
    service: IdentityService, db_session: AsyncSession
) -> None:
    from sqlalchemy import select

    from printorian.contexts.identity.models import User

    await _register(service)
    stored = await db_session.scalar(select(User))
    assert stored is not None
    assert PASSWORD not in stored.password_hash
    assert stored.password_hash.startswith("$argon2")


async def test_sign_in_returns_actor_with_resolved_permissions(service: IdentityService) -> None:
    await service.create_user(
        CreateUser(email="op@example.com", display_name="Op", password=PASSWORD, role=Role.OPERATOR)
    )
    granted = await service.sign_in(SignIn(email="op@example.com", password=PASSWORD))

    assert granted.actor.role is Role.OPERATOR
    assert granted.actor.can(Permission.OPERATE_PRINTER)
    assert not granted.actor.can(Permission.VIEW_FINANCIALS)


async def test_wrong_password_and_unknown_email_fail_identically(service: IdentityService) -> None:
    await _register(service, email="real@example.com")

    with pytest.raises(UnauthenticatedError) as wrong:
        await service.sign_in(SignIn(email="real@example.com", password="wrong-password"))
    with pytest.raises(UnauthenticatedError) as missing:
        await service.sign_in(SignIn(email="ghost@example.com", password=PASSWORD))

    # Identical codes: sign-in must not enumerate which accounts exist.
    assert wrong.value.code == missing.value.code == "error.identity.invalid_credentials"


async def test_token_is_stored_only_as_a_hash(
    service: IdentityService, db_session: AsyncSession
) -> None:
    from sqlalchemy import select

    from printorian.contexts.identity.models import Session

    await _register(service)
    granted = await service.sign_in(SignIn(email="a@example.com", password=PASSWORD))

    stored = await db_session.scalar(select(Session))
    assert stored is not None
    assert stored.token_hash != granted.token
    assert len(stored.token_hash) == 64


async def test_resolve_accepts_a_live_token(service: IdentityService) -> None:
    await _register(service)
    granted = await service.sign_in(SignIn(email="a@example.com", password=PASSWORD))
    actor = await service.resolve(granted.token)
    assert actor.email == "a@example.com"


async def test_resolve_rejects_expired_session(service: IdentityService, clock: FixedClock) -> None:
    await _register(service)
    granted = await service.sign_in(SignIn(email="a@example.com", password=PASSWORD))

    clock.advance(timedelta(hours=13))
    with pytest.raises(UnauthenticatedError) as excinfo:
        await service.resolve(granted.token)
    assert excinfo.value.code == "error.identity.session_expired"


async def test_sign_out_revokes_the_session(service: IdentityService) -> None:
    await _register(service)
    granted = await service.sign_in(SignIn(email="a@example.com", password=PASSWORD))

    await service.sign_out(granted.token)
    with pytest.raises(UnauthenticatedError):
        await service.resolve(granted.token)


async def test_deactivating_a_user_kills_their_live_sessions(service: IdentityService) -> None:
    user = await _register(service)
    granted = await service.sign_in(SignIn(email="a@example.com", password=PASSWORD))

    await service.set_active(user.id, is_active=False)  # type: ignore[attr-defined]
    with pytest.raises(UnauthenticatedError):
        await service.resolve(granted.token)


async def test_changing_password_revokes_existing_sessions(service: IdentityService) -> None:
    user = await _register(service)
    granted = await service.sign_in(SignIn(email="a@example.com", password=PASSWORD))

    await service.change_password(
        user.id,  # type: ignore[attr-defined]
        current=PASSWORD,
        replacement="a-brand-new-password",
    )
    with pytest.raises(UnauthenticatedError):
        await service.resolve(granted.token)


async def test_sign_in_publishes_events(service: IdentityService, bus: EventBus) -> None:
    await _register(service)
    async with bus.collecting() as events:
        await service.sign_in(SignIn(email="a@example.com", password=PASSWORD))
        with pytest.raises(UnauthenticatedError):
            await service.sign_in(SignIn(email="a@example.com", password="nope-nope-nope"))

    names = [e.name for e in events]
    assert "identity.sign_in_succeeded" in names
    assert "identity.sign_in_failed" in names
