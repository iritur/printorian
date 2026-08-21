"""The guards in front of the endpoints that cost something.

Three defects, one file, because they are the same defect wearing different
clothes — the API had no ceiling on how much work a caller could ask it to do:

* `POST /pricing/quote` takes an *optional* actor, so it is reachable from the
  public internet without signing in, and every call parses a mesh. There was no
  rate limit anywhere in the codebase.
* Sign-in counted failures into an event named "the raw material for lockout" and
  then did nothing with them.
* Uploads were read fully into memory and *then* measured against
  `max_upload_bytes`, while the settings comment beside that field claimed a large
  mesh is "refused before it is read into memory rather than after".

The suite at large runs with the ceilings lifted (`conftest.settings`) so ordinary
tests are not throttled by an accident of the harness. These build their own
`Settings` with real numbers, which is what makes them tests of the ceilings
rather than of the defaults.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.api.middleware import REQUEST_ID_HEADER
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Environment, Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api.test_auth_api import PASSWORD, _TestDatabase
from tests.conftest import wire_app

EMAIL = "guards-operator@example.com"


@pytest.fixture
def strict_settings(settings: Settings) -> Settings:
    """The same settings, with the ceilings a deployment actually runs."""
    return settings.model_copy(
        update={
            "quote_rate_per_minute": 2,
            "auth_rate_per_minute": 50,
            "upload_rate_per_minute": 2,
            "signin_max_attempts": 3,
            "max_upload_bytes": 2048,
        }
    )


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    strict_settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(strict_settings)
    database = _TestDatabase(strict_settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    wire_app(
        app,
        settings=strict_settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http

    await database.dispose()


@pytest.fixture
async def an_account(
    strict_settings: Settings, clock: FixedClock, bus: EventBus, clean_database: None
) -> None:
    database = _TestDatabase(strict_settings.database_url)
    async for session in database.session():
        await IdentityService(session, strict_settings, clock, bus).create_user(
            CreateUser(email=EMAIL, display_name="Operator", password=PASSWORD, role=Role.OPERATOR)
        )
    await database.dispose()


# ---------------------------------------------------------------- rate limits


async def test_quoting_past_the_ceiling_is_refused(client: AsyncClient) -> None:
    """The endpoint an unauthenticated caller can spend the farm's CPU on."""
    files = {"model": ("cube.stl", b"not really a mesh", "model/stl")}
    data = {"material_code": "PLA-BLACK"}

    # Two are allowed; what they answer does not matter here — a malformed mesh
    # is a 422, and the ceiling is counted before the work either way.
    for _ in range(2):
        await client.post("/pricing/quote", files=files, data=data)

    refused = await client.post("/pricing/quote", files=files, data=data)

    assert refused.status_code == 429
    assert refused.json()["code"] == "error.rate_limited"
    # So a well-behaved client backs off by the right amount instead of guessing.
    assert int(refused.headers["Retry-After"]) >= 1


async def test_previews_spend_the_same_allowance_as_quotes(client: AsyncClient) -> None:
    """Otherwise the ceiling is bypassed by asking the cheaper-looking question.

    Both parse the same mesh, so they cost the same and share one bucket.
    """
    files = {"model": ("cube.stl", b"not really a mesh", "model/stl")}
    data = {"material_code": "PLA-BLACK"}

    for _ in range(2):
        await client.post("/pricing/quote", files=files, data=data)

    refused = await client.post("/pricing/preview", files=files, data={**data, "to_quantity": "5"})

    assert refused.status_code == 429


# ---------------------------------------------------------------- lockout


async def test_repeated_bad_passwords_lock_the_pair_out(
    client: AsyncClient, an_account: None
) -> None:
    wrong = {"email": EMAIL, "password": "not-the-password"}

    for _ in range(3):
        rejected = await client.post("/auth/sign-in", json=wrong)
        assert rejected.status_code == 401

    locked = await client.post("/auth/sign-in", json=wrong)

    assert locked.status_code == 429
    assert locked.json()["code"] == "error.identity.locked_out"


async def test_the_lock_outlasts_the_right_password(client: AsyncClient, an_account: None) -> None:
    """A lock that the correct password clears is a lock that guessing defeats.

    The attacker who guesses right on attempt four is exactly who it is for.
    """
    for _ in range(3):
        await client.post("/auth/sign-in", json={"email": EMAIL, "password": "wrong"})

    refused = await client.post("/auth/sign-in", json={"email": EMAIL, "password": PASSWORD})

    assert refused.status_code == 429


async def test_a_good_password_before_the_limit_forgets_the_failures(
    client: AsyncClient, an_account: None
) -> None:
    await client.post("/auth/sign-in", json={"email": EMAIL, "password": "wrong"})
    await client.post("/auth/sign-in", json={"email": EMAIL, "password": "wrong"})

    accepted = await client.post("/auth/sign-in", json={"email": EMAIL, "password": PASSWORD})
    assert accepted.status_code == 200

    # The count is back to zero: two more mistakes do not reach the limit.
    for _ in range(2):
        rejected = await client.post("/auth/sign-in", json={"email": EMAIL, "password": "wrong"})
        assert rejected.status_code == 401


# ---------------------------------------------------------------- body size


async def test_an_over_sized_body_is_refused_before_it_is_buffered(
    client: AsyncClient,
) -> None:
    """413 from the declared length, without the body ever being read.

    `httpx` sends `Content-Length` for a byte payload, which is what every real
    client does, so this is the path that matters.

    Sized past `max_upload_bytes` *plus* the multipart allowance
    (`BodySizeLimitMiddleware.OVERHEAD_BYTES`), because the middleware bounds what
    is buffered rather than what the decoded part measures — the endpoint's own
    check below is what enforces the exact figure.
    """
    oversized = b"x" * (2048 + 2 * 1024 * 1024)

    refused = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", oversized, "model/stl")},
        data={"material_code": "PLA-BLACK"},
    )

    assert refused.status_code == 413
    assert refused.json()["code"] == "error.payload_too_large"


async def test_the_endpoint_still_enforces_the_exact_upload_limit(
    client: AsyncClient,
) -> None:
    """Between the two ceilings: buffered by the middleware, refused by the route.

    The middleware's allowance is deliberately loose — it exists to stop a body
    being read into memory at all — so the precise `max_upload_bytes` rule stays
    where it can see the decoded part.
    """
    over_the_route_limit = b"x" * 4096  # `max_upload_bytes` is 2048 here

    refused = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", over_the_route_limit, "model/stl")},
        data={"material_code": "PLA-BLACK"},
    )

    assert refused.status_code == 413
    assert refused.json()["code"] == "error.catalog.upload_too_large"


async def test_a_body_within_the_limit_still_reaches_the_endpoint(
    client: AsyncClient,
) -> None:
    """The guard must not be a ceiling on everything.

    A 422 here is the endpoint rejecting an unreadable mesh, which means the
    request got past the middleware and was handled.
    """
    answered = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", b"far too small to be a mesh", "model/stl")},
        data={"material_code": "PLA-BLACK"},
    )

    assert answered.status_code == 422


# ---------------------------------------------------------------- correlation


async def test_every_response_carries_a_correlation_id(client: AsyncClient) -> None:
    """`core.logging` claimed every line carried one. Nothing ever set it."""
    answered = await client.get("/health")

    assert answered.headers[REQUEST_ID_HEADER]


async def test_a_trace_begun_at_the_proxy_continues(client: AsyncClient) -> None:
    answered = await client.get("/health", headers={REQUEST_ID_HEADER: "from-the-edge"})

    assert answered.headers[REQUEST_ID_HEADER] == "from-the-edge"


async def test_an_unusable_inbound_id_is_replaced_rather_than_echoed(
    client: AsyncClient,
) -> None:
    """A control character in an id is how a forged one fakes a second log entry."""
    answered = await client.get("/health", headers={REQUEST_ID_HEADER: "ab"})

    assert answered.headers[REQUEST_ID_HEADER] != "ab"


# ---------------------------------------------------------------- environment


def test_the_relay_is_on_by_default() -> None:
    """A farm that forgets to configure it still gets live boards.

    The defect this guards against is silent: without the relay nothing errors,
    the console simply stops updating.
    """
    assert Settings(environment=Environment.LOCAL).events_relay_enabled is True


async def test_a_forged_forwarded_header_does_not_buy_more_allowance(
    client: AsyncClient,
) -> None:
    """`X-Forwarded-For` is a list the caller writes the front of.

    Keying a ceiling on the *first* entry — which is what `client_ip` reports,
    correctly, for a security screen — would make it bypassable with one header
    per request. That is worse than having no ceiling, because it looks like one.
    The last entry is the peer the nearest proxy actually saw.
    """
    files = {"model": ("cube.stl", b"not really a mesh", "model/stl")}
    data = {"material_code": "PLA-BLACK"}

    for index in range(2):
        await client.post(
            "/pricing/quote",
            files=files,
            data=data,
            headers={"X-Forwarded-For": f"10.0.0.{index}, 203.0.113.9"},
        )

    # A third forged origin, same real peer at the end of the chain.
    refused = await client.post(
        "/pricing/quote",
        files=files,
        data=data,
        headers={"X-Forwarded-For": "10.0.0.99, 203.0.113.9"},
    )

    assert refused.status_code == 429


async def test_two_genuinely_different_peers_keep_their_own_allowance(
    client: AsyncClient,
) -> None:
    """The converse, so the fix above is a key change and not a global counter."""
    files = {"model": ("cube.stl", b"not really a mesh", "model/stl")}
    data = {"material_code": "PLA-BLACK"}

    for _ in range(2):
        await client.post(
            "/pricing/quote", files=files, data=data, headers={"X-Forwarded-For": "203.0.113.9"}
        )

    answered = await client.post(
        "/pricing/quote", files=files, data=data, headers={"X-Forwarded-For": "198.51.100.4"}
    )

    assert answered.status_code != 429
