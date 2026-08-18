"""HTTP-level tests.

The point of this file is the authorization boundary. V1 hid financial screens by
not rendering them, which any client could ignore. Here the gate is server-side, so
these tests assert what an attacker would actually hit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore

PASSWORD = "correct-horse-battery"


class _TestDatabase:
    """Stands in for ``core.db.Database`` with the SQLite engine the suite uses."""

    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, poolclass=NullPool)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)

    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    app.state.settings = settings
    app.state.clock = clock
    app.state.event_bus = bus
    app.state.database = database
    app.state.object_store = object_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http

    await database.dispose()


async def _make_user(settings: Settings, clock: FixedClock, bus: EventBus, role: Role) -> str:
    """Create a user directly and return their email."""
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    email = f"{role.value}@example.com"
    async with factory() as session:
        service = IdentityService(session, settings, clock, bus)
        await service.create_user(
            CreateUser(email=email, display_name=role.value, password=PASSWORD, role=role)
        )
        await session.commit()
    await engine.dispose()
    return email


async def _token(client: AsyncClient, email: str) -> str:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    token: str = response.json()["token"]
    return token


# ------------------------------------------------------------------ health


async def test_health_is_open(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_each_dependency(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


# ------------------------------------------------------------------ auth


async def test_register_then_sign_in(client: AsyncClient) -> None:
    created = await client.post(
        "/auth/register",
        json={"email": "New@Example.com", "display_name": "New", "password": PASSWORD},
    )
    assert created.status_code == 201
    assert created.json()["email"] == "new@example.com"

    signed_in = await client.post(
        "/auth/sign-in", json={"email": "new@example.com", "password": PASSWORD}
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["actor"]["role"] == "customer"


async def test_self_registration_cannot_grant_a_staff_role(client: AsyncClient) -> None:
    """The public endpoint must ignore an attacker-supplied role."""
    response = await client.post(
        "/auth/register",
        json={
            "email": "sneaky@example.com",
            "display_name": "Sneaky",
            "password": PASSWORD,
            "role": "owner",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "customer"


async def test_duplicate_registration_returns_conflict_code(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "display_name": "Dup", "password": PASSWORD}
    assert (await client.post("/auth/register", json=payload)).status_code == 201

    conflict = await client.post("/auth/register", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "error.identity.email_taken"


async def test_bad_credentials_return_401_with_a_code_not_a_sentence(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/auth/sign-in", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "error.identity.invalid_credentials"
    # ADR-0012: no localized prose crosses the wire.
    assert set(body) == {"code", "details"}


async def test_me_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_returns_resolved_permissions(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    email = await _make_user(settings, clock, bus, Role.OPERATOR)
    token = await _token(client, email)

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    actor = response.json()
    assert actor["role"] == "operator"
    assert "operate_printer" in actor["permissions"]
    assert "view_financials" not in actor["permissions"]


async def test_sign_out_invalidates_the_token(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    email = await _make_user(settings, clock, bus, Role.MANAGER)
    token = await _token(client, email)
    auth = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/auth/me", headers=auth)).status_code == 200
    assert (await client.post("/auth/sign-out", headers=auth)).status_code == 204
    assert (await client.get("/auth/me", headers=auth)).status_code == 401


# ------------------------------------------------------ authorization gates


async def test_user_administration_is_closed_to_anonymous_callers(client: AsyncClient) -> None:
    assert (await client.get("/users")).status_code == 401


@pytest.mark.parametrize("role", [Role.CUSTOMER, Role.OPERATOR, Role.ENGINEER, Role.MANAGER])
async def test_only_owner_may_administer_users(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus, role: Role
) -> None:
    email = await _make_user(settings, clock, bus, role)
    token = await _token(client, email)

    response = await client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["code"] == "error.permission_denied"


async def test_owner_may_administer_users(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    email = await _make_user(settings, clock, bus, Role.OWNER)
    token = await _token(client, email)

    response = await client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert any(u["email"] == email for u in response.json())


# ------------------------------------------------- validation envelope


async def test_a_rejected_field_uses_the_same_error_envelope(client: AsyncClient) -> None:
    """Every error body carries a code (ADR-0012).

    Pydantic's native shape is ``{"detail": [...]}`` with no code, which the
    clients cannot translate — they fall back to "internal error", so a user who
    simply typed a short password was told the server had broken.
    """
    response = await client.post(
        "/auth/register",
        json={"email": "new@example.com", "display_name": "New", "password": "short"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "error.validation.password.string_too_short"
    assert body["details"]["field"] == "password"
    assert body["details"]["limit"] == 10


async def test_the_validation_code_degrades_through_its_prefixes(client: AsyncClient) -> None:
    """`error.validation.<field>.<rule>` so a client's prefix fallback can render
    the field's message, or the generic one, without an exhaustive mapping."""
    response = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "display_name": "New", "password": "long-enough-1"},
    )

    body = response.json()
    assert body["code"].startswith("error.validation.email.")
    assert body["details"]["field"] == "email"


async def test_every_offending_field_is_reported(client: AsyncClient) -> None:
    """So a form can mark all of its bad inputs, not just the first."""
    response = await client.post("/auth/register", json={})

    body = response.json()
    assert set(body["details"]["fields"]) >= {"email", "display_name", "password"}


async def test_a_validation_failure_never_leaks_an_unserializable_context(
    client: AsyncClient,
) -> None:
    """Pydantic's `ctx` can hold exception instances; putting one in a JSON
    response would turn a 422 into a 500 inside the error handler itself."""
    response = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "display_name": "New", "password": "long-enough-1"},
    )

    assert response.status_code == 422
    assert "ctx" not in response.json()["details"]


# ------------------------------------------------------- self-lockout guard


async def test_an_owner_cannot_change_their_own_role(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """A sole owner demoting themselves leaves a farm with no owner at all, and
    `manage_users` is then unreachable without editing the database by hand."""
    email = await _make_user(settings, clock, bus, Role.OWNER)
    token = await _token(client, email)
    headers = {"Authorization": f"Bearer {token}"}

    me = (await client.get("/auth/me", headers=headers)).json()
    response = await client.put(
        f"/users/{me['user_id']}/role", params={"role": "customer"}, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == "error.identity.cannot_change_own_role"


async def test_an_owner_cannot_deactivate_themselves(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Deactivation revokes every session, so this signs the caller out of the
    screen they did it from."""
    email = await _make_user(settings, clock, bus, Role.OWNER)
    token = await _token(client, email)
    headers = {"Authorization": f"Bearer {token}"}

    me = (await client.get("/auth/me", headers=headers)).json()
    response = await client.put(
        f"/users/{me['user_id']}/active", params={"is_active": "false"}, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == "error.identity.cannot_deactivate_self"


async def test_an_owner_may_still_administer_everyone_else(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The guard is about the caller, not about the operation."""
    owner_email = await _make_user(settings, clock, bus, Role.OWNER)
    other_email = await _make_user(settings, clock, bus, Role.OPERATOR)
    headers = {"Authorization": f"Bearer {await _token(client, owner_email)}"}

    users = (await client.get("/users", headers=headers)).json()
    other = next(u for u in users if u["email"] == other_email)

    promoted = await client.put(
        f"/users/{other['id']}/role", params={"role": "manager"}, headers=headers
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "manager"

    disabled = await client.put(
        f"/users/{other['id']}/active", params={"is_active": "false"}, headers=headers
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False


async def test_setting_your_own_role_to_what_it_already_is_is_harmless(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """A no-op must not be refused — a UI that re-submits an unchanged form
    should not produce an error the user cannot act on."""
    email = await _make_user(settings, clock, bus, Role.OWNER)
    headers = {"Authorization": f"Bearer {await _token(client, email)}"}
    me = (await client.get("/auth/me", headers=headers)).json()

    response = await client.put(
        f"/users/{me['user_id']}/role", params={"role": "owner"}, headers=headers
    )
    assert response.status_code == 200
