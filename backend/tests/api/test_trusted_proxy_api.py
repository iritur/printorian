"""The proxy rule over HTTP: a header from an untrusted peer buys nothing (§5(a)).

The test transport's peer is `127.0.0.1`. With `trusted_proxies` emptied, that
peer is *not* a proxy the API believes, so every `X-Forwarded-For` below is a
caller's own claim. Two readers depend on the answer, and both are pinned:

* **the rate ceiling** keys on the peer, so varying the forged header does not
  buy a fresh allowance;
* **the YooKassa webhook** checks the peer, so a forged header naming one of
  YooKassa's own addresses is refused — the hole the old first-entry read left
  open the day the API was reached without Caddy in front (security review F3).

`test_guards_api.py` keeps the other half — through a trusted proxy (the
default, loopback), the last entry is the address that counts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from printorian.api.app import create_app
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api.test_auth_api import _TestDatabase
from tests.conftest import wire_app


@pytest.fixture
def untrusting(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "trusted_proxies": [],
            "quote_rate_per_minute": 2,
            "yookassa_shop_id": "123456",
            "yookassa_secret_key": SecretStr("test_secret"),
        }
    )


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    untrusting: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(untrusting)
    database = _TestDatabase(untrusting.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    wire_app(
        app, settings=untrusting, clock=clock, bus=bus, database=database, object_store=object_store
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http
    await database.dispose()


async def test_a_forged_header_from_an_untrusted_peer_buys_no_allowance(
    client: AsyncClient,
) -> None:
    files = {"model": ("cube.stl", b"not really a mesh", "model/stl")}
    data = {"material_code": "PLA-BLACK"}

    for index in range(2):
        await client.post(
            "/pricing/quote",
            files=files,
            data=data,
            headers={"X-Forwarded-For": f"198.51.100.{index}"},
        )

    # A third "different" address — all three are the same peer.
    refused = await client.post(
        "/pricing/quote", files=files, data=data, headers={"X-Forwarded-For": "198.51.100.99"}
    )

    assert refused.status_code == 429


async def test_a_forged_yookassa_address_does_not_pass_the_webhook_allow_list(
    client: AsyncClient,
) -> None:
    refused = await client.post(
        "/payments/webhook/yookassa",
        headers={"X-Forwarded-For": "185.71.76.1"},
        content=b'{"event": "payment.succeeded", "object": {"id": "p1", "status": "succeeded"}}',
    )

    assert refused.status_code >= 400, refused.text
    assert refused.json()["code"] == "error.payments.webhook_untrusted_source"
    # The address it was judged on is the peer, not the header.
    assert refused.json()["details"]["source"] == "127.0.0.1"
