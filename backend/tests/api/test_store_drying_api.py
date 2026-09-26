"""Drying over HTTP: the state reaches the cell panel, and the settings reach the state.

What only the edge can prove: that `GET /store/cells/{address}` carries the
state per lot, that the two writes sit behind `MANAGE_INVENTORY`, and that
`inventory.drying_valid_hours` — a settings row nothing read until this slice —
is what the state is computed against.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app

PASSWORD = "correct-horse-battery"
PA_LOT = new_id()
PLA_LOT = new_id()


class _TestDatabase:
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

    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (
            ("boss@example.com", Role.MANAGER),
            ("op@example.com", Role.OPERATOR),
            # Settings are the owner's to edit; a manager may run the store but not
            # change its rules, and the second test turns the rule off.
            ("owner@example.com", Role.OWNER),
        ):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        for lot_id, code, family in (
            (PA_LOT, "PA-CF-STORE", "PA-CF"),
            (PLA_LOT, "PLA-STORE", "PLA"),
        ):
            spec_id = new_id()
            session.add(
                MaterialSpec(
                    id=spec_id,
                    code=code,
                    name=code,
                    family=family,
                    sell_price_per_gram=Decimal("2.50"),
                )
            )
            await session.flush()
            session.add(
                MaterialLot(
                    id=lot_id,
                    spec_id=spec_id,
                    label=f"{code}-001",
                    initial_grams=Decimal(1000),
                    remaining_grams=Decimal(1000),
                )
            )
        await session.commit()

    wire_app(
        app, settings=settings, clock=clock, bus=bus, database=database, object_store=object_store
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http
    await database.dispose()


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def shelve(client: AsyncClient, manager: dict[str, str]) -> None:
    await client.post("/store/zones", json={"code": "D", "name": "Сухой шкаф"}, headers=manager)
    await client.post("/store/cells", json={"zone_code": "D", "address": "D1-1"}, headers=manager)
    for lot_id in (PA_LOT, PLA_LOT):
        placed = await client.post(
            f"/store/lots/{lot_id}/place", json={"address": "D1-1"}, headers=manager
        )
        assert placed.status_code == 200, placed.text


def lots_of(detail: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = detail["lots"]
    assert isinstance(rows, list)
    return {str(row["label"]): row for row in rows}


async def test_the_cell_panel_carries_the_state_and_the_writes_change_it(
    client: AsyncClient, clock: FixedClock
) -> None:
    manager = await auth(client, "boss@example.com")
    operator = await auth(client, "op@example.com")
    await shelve(client, manager)

    before = lots_of((await client.get("/store/cells/D1-1", headers=operator)).json())
    # Never marked: unknown, and no countdown invented from nowhere. PLA is outside
    # the rule altogether, whatever its mark.
    assert before["PA-CF-STORE-001"]["drying"] == {
        "state": "unknown",
        "dried_at": None,
        "valid_until": None,
        "hours_left": None,
    }
    assert before["PLA-STORE-001"]["drying"]["state"] == "not_required"

    # The writes are a manager's. An operator finds the spool; a manager sends it.
    refused = await client.post(f"/store/lots/{PA_LOT}/dry", json={}, headers=operator)
    assert refused.status_code == 403

    sent = await client.post(f"/store/lots/{PA_LOT}/dry", json={}, headers=manager)
    assert sent.status_code == 200, sent.text
    assert sent.json()["location_kind"] == "dryer"
    assert sent.json()["cell"] == "D1-1"
    during = lots_of((await client.get("/store/cells/D1-1", headers=operator)).json())
    assert during["PA-CF-STORE-001"]["drying"]["state"] == "drying"

    # Each jump of the clock outlives the sign-in token, so both sign in again:
    # the session lifetime is the identity context's rule and not this test's.
    clock.advance(timedelta(hours=8))
    manager = await auth(client, "boss@example.com")
    dried = await client.post(
        f"/store/lots/{PA_LOT}/dried", json={"note": "8 ч при 70 °C"}, headers=manager
    )
    assert dried.status_code == 200, dried.text

    clock.advance(timedelta(hours=30))
    operator = await auth(client, "op@example.com")
    after = (await client.get("/store/cells/D1-1", headers=operator)).json()
    pa = lots_of(after)["PA-CF-STORE-001"]["drying"]
    assert isinstance(pa, dict)
    assert pa["state"] == "dry"
    assert Decimal(str(pa["hours_left"])) == Decimal("42.0")
    assert after["drying_valid_hours"] == 72

    feed = (await client.get(f"/store/movements?lot_id={PA_LOT}", headers=operator)).json()
    assert [row["reason"] for row in feed][:2] == ["stock.dried", "stock.to_dryer"]
    assert feed[0]["note"] == "8 ч при 70 °C"


async def test_the_window_is_read_off_the_settings_screen(
    client: AsyncClient, clock: FixedClock
) -> None:
    """`inventory.drying_valid_hours` changes the answer — the setting is read, not stored."""
    manager = await auth(client, "boss@example.com")
    await shelve(client, manager)
    await client.post(f"/store/lots/{PA_LOT}/dry", json={}, headers=manager)
    await client.post(f"/store/lots/{PA_LOT}/dried", json={}, headers=manager)
    clock.advance(timedelta(hours=30))
    manager = await auth(client, "boss@example.com")

    good = lots_of((await client.get("/store/cells/D1-1", headers=manager)).json())
    assert good["PA-CF-STORE-001"]["drying"]["state"] == "dry"

    owner = await auth(client, "owner@example.com")
    shortened = await client.put(
        "/settings/inventory.drying_valid_hours", json={"value": 24}, headers=owner
    )
    assert shortened.status_code == 200, shortened.text

    lapsed = (await client.get("/store/cells/D1-1", headers=manager)).json()
    assert lots_of(lapsed)["PA-CF-STORE-001"]["drying"]["state"] == "expired"
    assert lapsed["drying_valid_hours"] == 24

    switched_off = await client.put(
        "/settings/inventory.require_drying", json={"value": False}, headers=owner
    )
    assert switched_off.status_code == 200, switched_off.text
    off = (await client.get("/store/cells/D1-1", headers=manager)).json()
    assert lots_of(off)["PA-CF-STORE-001"]["drying"]["state"] == "not_required"
    assert off["drying_valid_hours"] is None


async def test_a_spool_never_sent_cannot_be_marked_dried(client: AsyncClient) -> None:
    manager = await auth(client, "boss@example.com")
    await shelve(client, manager)
    response = await client.post(f"/store/lots/{PA_LOT}/dried", json={}, headers=manager)
    assert response.status_code == 422
    assert response.json()["code"] == "error.inventory.lot_not_drying"
