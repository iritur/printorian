"""The non-filament store over HTTP (#35, `api/routers/store_articles.py`).

* **Reads are the floor's, writes are the manager's** — the store's own split:
  an operator sees the articles and their movements; only `MANAGE_INVENTORY`
  declares, places, issues or writes one off.
* **No money on any of it**, asserted over the whole body, for the reason
  `test_store_api.py` gives: the failure is a *new* field arriving.
* **`/movements` is reachable** — declared before the `/{article_id}` routes, which
  would otherwise take the word for an id and answer 422.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.inventory import ArticleKind, StockArticles
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api.test_auth_api import _TestDatabase
from tests.conftest import wire_app

PASSWORD = "correct-horse-battery"
MONEY_WORDS = ("price", "cost", "value", "rub", "₽", "money")


@pytest.fixture
async def client(
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async for session in database.session():
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (("boss@example.com", Role.MANAGER), ("op@example.com", Role.OPERATOR)):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
    wire_app(
        app, settings=settings, clock=clock, bus=bus, database=database, object_store=object_store
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http
    await database.dispose()


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def test_the_manager_stocks_and_moves_and_the_floor_reads(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    op = await auth(client, "op@example.com")
    assert (
        await client.post("/store/zones", json={"code": "C", "name": "Запчасти"}, headers=boss)
    ).status_code == 201
    assert (
        await client.post("/store/cells", json={"zone_code": "C", "address": "C5-1"}, headers=boss)
    ).status_code == 201

    declared = await client.post(
        "/store/articles",
        json={"kind": "spare_part", "code": "NZ-04", "name": "Сопло 0.4", "unit": "piece"},
        headers=boss,
    )
    assert declared.status_code == 201, declared.text
    article = declared.json()
    assert article["on_hand"] == "0"

    placed = await client.post(
        f"/store/articles/{article['id']}/place", json={"address": "C5-1"}, headers=boss
    )
    assert placed.status_code == 200, placed.text
    assert placed.json()["cell"] == "C5-1"

    # The floor reads everything — and is shown no money anywhere.
    listed = await client.get("/store/articles", headers=op)
    feed = await client.get("/store/articles/movements", headers=op)
    assert listed.status_code == 200 and feed.status_code == 200, feed.text
    assert [row["code"] for row in listed.json()] == ["NZ-04"]
    assert [row["reason"] for row in feed.json()] == ["stock.moved"]
    for body in (listed.text, feed.text):
        lowered = json.loads(body).__repr__().lower()
        assert not [word for word in MONEY_WORDS if word in lowered], body


async def test_the_floor_cannot_change_the_book(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    op = await auth(client, "op@example.com")
    article = (
        await client.post(
            "/store/articles", json={"kind": "packaging", "code": "box-a"}, headers=boss
        )
    ).json()

    refused = [
        await client.post(
            "/store/articles", json={"kind": "packaging", "code": "box-b"}, headers=op
        ),
        await client.post(
            f"/store/articles/{article['id']}/place", json={"address": "C5-1"}, headers=op
        ),
        await client.post(
            f"/store/articles/{article['id']}/issue", json={"quantity": "1"}, headers=op
        ),
        await client.post(
            f"/store/articles/{article['id']}/write-off", json={"quantity": "1"}, headers=op
        ),
    ]
    assert [response.status_code for response in refused] == [403, 403, 403, 403]


async def test_taking_more_than_the_book_holds_is_a_coded_refusal(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    article = (
        await client.post(
            "/store/articles", json={"kind": "post_consumable", "code": "IPA"}, headers=boss
        )
    ).json()

    refused = await client.post(
        f"/store/articles/{article['id']}/write-off", json={"quantity": "1"}, headers=boss
    )

    # 422, as every domain refusal here (`api/errors.py`), the spool write-off included.
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "error.inventory.take_exceeds_on_hand"


async def test_an_article_is_counted_in_its_own_unit_and_reported_in_both_measures(
    client: AsyncClient,
) -> None:
    """The stocktake's article line, and the two reports' article halves (#35)."""
    boss = await auth(client, "boss@example.com")
    op = await auth(client, "op@example.com")
    await client.post("/store/zones", json={"code": "C", "name": "Запчасти"}, headers=boss)
    await client.post("/store/cells", json={"zone_code": "C", "address": "C6-1"}, headers=boss)
    article = (
        await client.post(
            "/store/articles", json={"kind": "packaging", "code": "box-a"}, headers=boss
        )
    ).json()
    async with client._transport.app.state.database.session_factory() as session:  # type: ignore[attr-defined]
        await StockArticles(session).receive(
            ArticleKind.PACKAGING,
            "box-a",
            quantity=Decimal(12),
            at=datetime(2026, 1, 1, tzinfo=UTC),
            unit_cost=Decimal(62),
        )
        await session.commit()
    await client.post(
        f"/store/articles/{article['id']}/place", json={"address": "C6-1"}, headers=boss
    )

    opened = (await client.post("/store/stocktakes", json={}, headers=boss)).json()
    [line] = opened["article_lines"]
    assert (line["code"], line["expected"]) == ("box-a", "12.00")
    counted = await client.post(
        f"/store/stocktakes/{opened['id']}/articles/{article['id']}",
        json={"counted": "11"},
        headers=boss,
    )
    assert counted.status_code == 200, counted.text
    assert Decimal(counted.json()["article_lines"][0]["counted"]) == Decimal(11)

    turnover = await client.get("/store/turnover", headers=op)
    assert turnover.status_code == 200
    assert [row["kind"] for row in turnover.json()["article_rows"]] == ["packaging"]
    # The money half stays behind view_financials, for articles as for spools.
    assert (await client.get("/store/dead-stock", headers=op)).status_code == 403
    dead = await client.get("/store/dead-stock?idle_days=1", headers=boss)
    assert dead.status_code == 200
    assert "articles" in dead.json()
