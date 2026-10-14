"""API keys over HTTP: where a key opens a door, and every door it must not open.

What only the edge can prove (`identity.api_keys`, `api/deps.get_caller`):

* a key reads what it was issued for — the kit's floor screen reads production,
  its accounting export reads every order;
* a key is **not a person** — `/account`, the routes that act as their caller,
  refuse it, so a floor screen's key cannot export its creator's account;
* a key cannot mint keys, and revoking one stops it at the next request;
* nobody but the owner sees the list, and the list never carries a key.
"""

from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from printorian.api.app import create_app
from printorian.api.ws import Hub
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api.test_auth_api import PASSWORD, _TestDatabase
from tests.conftest import wire_app

PEOPLE = (("owner@example.com", Role.OWNER), ("op@example.com", Role.OPERATOR))


async def _seed(
    database: _TestDatabase, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async for session in database.session():
        identity = IdentityService(session, settings, clock, bus)
        for email, role in PEOPLE:
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )


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
    await _seed(database, settings, clock, bus)
    wire_app(
        app, settings=settings, clock=clock, bus=bus, database=database, object_store=object_store
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http
    await database.dispose()


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def a_key(client: AsyncClient, *permissions: str) -> str:
    issued = await client.post(
        "/api-keys",
        json={"name": "Экран цеха", "permissions": list(permissions)},
        headers=await auth(client, "owner@example.com"),
    )
    assert issued.status_code == 201, issued.text
    secret: str = issued.json()["secret"]
    return secret


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def test_a_key_reads_what_it_was_issued_for_and_nothing_else(client: AsyncClient) -> None:
    floor = bearer(await a_key(client, "view_production"))

    assert (await client.get("/store/cells", headers=floor)).status_code == 200
    # Every order is the accounting export's permission, not the floor screen's.
    refused = await client.get("/orders", headers=floor)
    assert refused.status_code == 403
    assert refused.json()["code"] == "error.permission_denied"

    export = bearer(await a_key(client, "view_all_orders"))
    assert (await client.get("/orders", headers=export)).status_code == 200


async def test_a_key_is_not_a_person(client: AsyncClient) -> None:
    """`/account` acts as its caller. A floor screen's key must not become its owner."""
    floor = bearer(await a_key(client, "view_production"))

    for path in ("/account", "/auth/me", "/orders/mine"):
        refused = await client.get(path, headers=floor)
        assert refused.status_code == 401, path
        assert refused.json()["code"] == "error.identity.api_key_not_accepted", path


async def test_a_key_cannot_mint_keys_and_a_revoked_one_stops(client: AsyncClient) -> None:
    owner = await auth(client, "owner@example.com")
    secret = await a_key(client, "view_production")

    minted = await client.post(
        "/api-keys", json={"name": "x", "permissions": ["view_production"]}, headers=bearer(secret)
    )
    assert minted.status_code == 403

    [listed] = (await client.get("/api-keys", headers=owner)).json()
    revoked = await client.post(f"/api-keys/{listed['id']}/revoke", headers=owner)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    stopped = await client.get("/store/cells", headers=bearer(secret))
    assert stopped.status_code == 401
    assert stopped.json()["code"] == "error.identity.invalid_api_key"


async def test_only_the_owner_sees_the_list_and_it_never_carries_a_key(
    client: AsyncClient,
) -> None:
    secret = await a_key(client, "view_production")

    refused = await client.get("/api-keys", headers=await auth(client, "op@example.com"))
    assert refused.status_code == 403

    listing = await client.get("/api-keys", headers=await auth(client, "owner@example.com"))
    assert listing.status_code == 200
    assert secret not in listing.text
    assert "key_hash" not in listing.text
    grantable = await client.get(
        "/api-keys/permissions", headers=await auth(client, "owner@example.com")
    )
    assert "manage_users" not in grantable.json()
    assert "view_production" in grantable.json()


@pytest.fixture
def sync_client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> Iterator[TestClient]:
    """The live stream needs a real loop, so a synchronous client, as in
    `test_events_ws.py`."""
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)
    asyncio.run(_seed(database, settings, clock, bus))
    wire_app(
        app, settings=settings, clock=clock, bus=bus, database=database, object_store=object_store
    )
    app.state.hub = Hub()
    app.state.hub.attach(bus)
    with TestClient(app) as http:
        yield http
    asyncio.run(database.dispose())
    gc.collect()


def test_the_floor_screen_key_opens_the_live_stream(sync_client: TestClient) -> None:
    """The kit's first example key is a floor screen, and the floor screen is this."""
    token = sync_client.post(
        "/auth/sign-in", json={"email": "owner@example.com", "password": PASSWORD}
    ).json()["token"]
    secret = sync_client.post(
        "/api-keys",
        json={"name": "Экран цеха", "permissions": ["view_production"]},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["secret"]

    with sync_client.websocket_connect(
        "/ws/events", subprotocols=["printorian.v1", f"bearer.{secret}"]
    ):
        pass
