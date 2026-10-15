"""A stranger's upload has its own, smaller ceiling (security review F5, §5(c)).

`POST /pricing/quote` and `/pricing/preview` need no account, and parsing an
ASCII STL costs five to ten times its size in memory. The owner chose a separate
limit for anonymous callers — `anonymous_upload_bytes` — while a signed-in
customer keeps `max_upload_bytes`. Pinned here, with the two set apart so the
difference is visible (2 KiB and 8 KiB):

* **an anonymous caller is refused above the anonymous ceiling**, on both
  endpoints that parse a mesh;
* **a signed-in caller with the same file is not** — the request reaches the
  parser, which answers 422 for a body that is not a mesh;
* **the public stats state the anonymous figure**, because the promo page
  promises «расчёт без регистрации» up to it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api.test_auth_api import PASSWORD, _TestDatabase
from tests.conftest import wire_app

EMAIL = "big-files@example.com"
FILE = b"x" * 4096  # above the anonymous 2 KiB, below the signed-in 8 KiB


@pytest.fixture
def ceilings(settings: Settings) -> Settings:
    return settings.model_copy(update={"max_upload_bytes": 8192, "anonymous_upload_bytes": 2048})


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    ceilings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(ceilings)
    database = _TestDatabase(ceilings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async for session in database.session():
        await IdentityService(session, ceilings, clock, bus).create_user(
            CreateUser(email=EMAIL, display_name="Customer", password=PASSWORD, role=Role.CUSTOMER)
        )
    wire_app(
        app, settings=ceilings, clock=clock, bus=bus, database=database, object_store=object_store
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http
    await database.dispose()


async def signed_in(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.mark.parametrize("path", ["/pricing/quote", "/pricing/preview"])
async def test_a_stranger_is_held_to_the_anonymous_ceiling(client: AsyncClient, path: str) -> None:
    refused = await client.post(
        path,
        files={"model": ("part.stl", FILE, "model/stl")},
        data={"material_code": "PLA-BLACK"},
    )

    assert refused.status_code == 413, refused.text
    assert refused.json()["code"] == "error.catalog.upload_too_large"
    assert refused.json()["details"]["limit"] == 2048


@pytest.mark.parametrize("path", ["/pricing/quote", "/pricing/preview"])
async def test_a_signed_in_customer_keeps_the_full_ceiling(client: AsyncClient, path: str) -> None:
    answered = await client.post(
        path,
        files={"model": ("part.stl", FILE, "model/stl")},
        data={"material_code": "PLA-BLACK"},
        headers=await signed_in(client),
    )

    # Past the size check and into the parser, which refuses a body that is not
    # a mesh — the point is that it was not a 413.
    assert answered.status_code == 422, answered.text


async def test_the_promo_page_is_told_the_anonymous_figure(client: AsyncClient) -> None:
    stats = await client.get("/public/stats")

    assert stats.status_code == 200, stats.text
    assert stats.json()["anonymous_upload_bytes"] == 2048
    assert stats.json()["max_upload_bytes"] == 8192
