"""«Поставщики» over HTTP — the scorecard the board serves is computed.

Issue #34's last clause: *the supplier scorecard computes from delivered POs
rather than being entered by hand*. `tests/unit/test_procurement_scorecard.py`
takes the arithmetic apart; this drives one order through the real routes to the
shelf and reads the row the board then carries. The order is raised without an
`expected_at`, so the case here is the one a screen gets wrong most quietly: a
delivery with no date is a delivery and nothing else — `dated` is 0 and the share
is null, not 0%.

The board's money rule is not restated: `test_purchasing_api.py` asserts on the
schema that `PurchasingBoard` carries no money at any depth, and the scorecard
rows are part of that schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._purchasing_support import PurchasingDatabase, an_order, auth, seed_desk
from tests.conftest import wire_app


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[PurchasingDatabase]:
    database = PurchasingDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    database: PurchasingDatabase,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with database.session_factory() as session:
        await seed_desk(session, settings, clock, bus)

    wire_app(
        app,
        settings=settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http


async def test_the_board_scores_a_supplier_from_what_reached_the_shelf(
    client: AsyncClient,
) -> None:
    """One supplier, one order walked to «На складе», one delivery on the row.

    The supplier is on the board *before* anything arrives, with zeros — the
    chips rule: a supplier missing from the table is a gap somebody has to
    notice, where a row reading «0» is a fact.
    """
    headers = await auth(client)
    supplier = (
        await client.post(
            "/purchasing/suppliers",
            json={"code": "FILAMENT-RU", "name": "ТехноПласт", "kinds": ["material"]},
            headers=headers,
        )
    ).json()
    before = (await client.get("/purchasing/board", headers=headers)).json()
    assert [(row["code"], row["deliveries"]) for row in before["suppliers"]] == [("FILAMENT-RU", 0)]
    assert before["suppliers"][0]["on_time_share"] is None

    order = await an_order(client)
    assigned = await client.post(
        f"/purchasing/orders/{order['id']}/supplier",
        json={"supplier_id": supplier["id"]},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.json()
    for stage in ("receiving", "stored"):
        moved = await client.post(
            f"/purchasing/orders/{order['id']}/status", json={"to": stage}, headers=headers
        )
        assert moved.status_code == 200, moved.json()

    board = (await client.get("/purchasing/board", headers=headers)).json()

    [row] = board["suppliers"]
    assert row["code"] == "FILAMENT-RU"
    assert row["name"] == "ТехноПласт"
    assert row["deliveries"] == 1
    assert row["dated"] == 0
    assert row["on_time"] == 0
    assert row["on_time_share"] is None
    assert row["last_delivery_at"] is not None


async def test_a_cancelled_order_is_not_a_delivery(client: AsyncClient) -> None:
    """Cancelled is not late. The row keeps its zero and its null."""
    headers = await auth(client)
    supplier = (
        await client.post(
            "/purchasing/suppliers",
            json={"code": "FILAMENT-RU", "name": "ТехноПласт"},
            headers=headers,
        )
    ).json()
    order = await an_order(client)
    await client.post(
        f"/purchasing/orders/{order['id']}/supplier",
        json={"supplier_id": supplier["id"]},
        headers=headers,
    )
    cancelled = await client.post(f"/purchasing/orders/{order['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.json()

    [row] = (await client.get("/purchasing/board", headers=headers)).json()["suppliers"]

    assert row["deliveries"] == 0
    assert row["on_time_share"] is None
    assert row["last_delivery_at"] is None
