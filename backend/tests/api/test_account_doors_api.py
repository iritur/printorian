"""Two doors on `/account` the security review (#27) found unguarded.

Both are about a *stolen session*: an attacker holding a customer's or an owner's
cookie, without their password. The first door let them guess the password behind
the session at whatever rate the hasher allowed; the second let an owner's cookie
deactivate the farm's only `manage_users` holder with one request and no
confirmation. Split from `test_account_api.py` at the 400-line gate, and at a real
seam — that file is about scoping, this one is about abuse.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import PASSWORD, a_shop, token_for


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async for http in a_shop(object_store, settings, clock, bus):
        yield http


async def test_guessing_the_current_password_is_locked_out_like_a_sign_in(
    client: AsyncClient, settings: Settings
) -> None:
    """A stolen session used to be a free oracle for the password behind it."""
    auth = await token_for(client, "buyer@example.com")

    for _ in range(settings.signin_max_attempts):
        wrong = await client.post(
            "/account/password",
            json={"current": "not-it", "replacement": "a-much-better-secret"},
            headers=auth,
        )
        assert wrong.status_code == 401

    locked = await client.post(
        "/account/password",
        json={"current": PASSWORD, "replacement": "a-much-better-secret"},
        headers=auth,
    )
    # Even the right password is refused now — the count, not the credential.
    assert locked.status_code == 429
    assert locked.json()["code"] == "error.identity.locked_out"
    assert (await client.get("/account", headers=auth)).status_code == 200


async def test_staff_cannot_close_their_own_account_through_the_customer_door(
    client: AsyncClient,
) -> None:
    """`/users` refuses an owner deactivating themselves; this route used to do it
    with a stolen cookie and one request. A customer still can."""
    owner = await token_for(client, "boss@example.com")

    refused = await client.post("/account/close", headers=owner)
    assert refused.status_code == 422
    assert refused.json()["code"] == "error.identity.staff_account_closed_by_owner"
    assert (await client.get("/auth/me", headers=owner)).status_code == 200
