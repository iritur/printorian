"""The maintenance table over HTTP: a new machine's card, and an operation's default.

Issue #29's maintenance-intervals row, at the edge where it is read. Both routes
resolve `service.maintenance_defaults` through `FarmSettings`, so what these
prove is the wiring — that editing the table in settings changes what the next
`POST /printers` and the next `POST /printers/{id}/services` do. A table that
parsed, stored and resolved but reached neither route would pass every unit test
and change nothing on the farm.

Its own module rather than more of `test_printers_api.py`, which is 362 lines
against the 400-line gate; the client fixture is the same shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.settings import SettingsService
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app

PASSWORD = "correct-horse-battery"
KEY = "service.maintenance_defaults"


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
async def database(settings: Settings, clean_database: None) -> AsyncIterator[_TestDatabase]:
    database = _TestDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def client(
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
    database: _TestDatabase,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, bus)
        await identity.create_user(
            CreateUser(
                email="boss@example.com", display_name="boss", password=PASSWORD, role=Role.OWNER
            )
        )
        await session.commit()

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


async def auth(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/auth/sign-in", json={"email": "boss@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def register(client: AsyncClient, headers: dict[str, str], name: str) -> dict[str, Any]:
    response = await client.post(
        "/printers", json={"name": name, "connection_mode": "manual"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def set_table(database: _TestDatabase, clock: FixedClock, rows: list[dict[str, Any]]) -> None:
    """Store the table the way the settings screen does, through the service."""
    async with database.session_factory() as session:
        await SettingsService(session, clock).set_value(KEY, rows, by=None)
        await session.commit()


def card(printer: dict[str, Any]) -> dict[str, int]:
    return {op["kind"]: op["interval_hours"] for op in printer["services"]}


async def test_a_new_machine_gets_the_default_card_when_the_table_is_untouched(
    client: AsyncClient,
) -> None:
    """Every kind at 500 hours — the card the code always implied and never made."""
    created = await register(client, await auth(client), "p1s-01")

    assert card(created) == {
        "nozzle_change": 500,
        "belt_tension": 500,
        "lubrication": 500,
        "bed_level": 500,
        "filter_change": 500,
        "deep_clean": 500,
    }


async def test_editing_the_table_changes_what_the_next_machine_gets(
    client: AsyncClient, database: _TestDatabase, clock: FixedClock
) -> None:
    """The wiring test. A row dropped from the table is not seeded; a row edited
    seeds at its new figure; and a machine registered *before* the edit keeps
    the card it was given — the table applies to new machines, as the kit says."""
    headers = await auth(client)
    before = await register(client, headers, "p1s-01")

    await set_table(
        database,
        clock,
        [
            {"code": "nozzle_change", "interval_hours": 300},
            {"code": "deep_clean", "interval_hours": 1500},
        ],
    )
    after = await register(client, headers, "p1s-02")

    assert card(after) == {"nozzle_change": 300, "deep_clean": 1500}
    unchanged = (await client.get(f"/printers/{before['id']}", headers=headers)).json()
    assert card(unchanged)["nozzle_change"] == 500


async def test_an_operation_added_without_a_periodicity_takes_its_kinds_row(
    client: AsyncClient, database: _TestDatabase, clock: FixedClock
) -> None:
    """Omitted is the table's figure; explicit is the engineer's; a kind the
    table does not carry keeps the schema's 500 rather than being refused."""
    headers = await auth(client)
    await set_table(database, clock, [{"code": "nozzle_change", "interval_hours": 300}])
    printer = await register(client, headers, "p1s-03")
    # The seeded row is removed from the question: the card starts with only
    # `nozzle_change`, so the three additions below are all new rows.
    assert card(printer) == {"nozzle_change": 300}

    omitted = await client.post(
        f"/printers/{printer['id']}/services", json={"kind": "lubrication"}, headers=headers
    )
    explicit = await client.post(
        f"/printers/{printer['id']}/services",
        json={"kind": "bed_level", "interval_hours": 250},
        headers=headers,
    )
    assert omitted.status_code == 201, omitted.text
    assert explicit.status_code == 201, explicit.text

    await set_table(database, clock, [{"code": "lubrication", "interval_hours": 900}])
    from_table = await client.post(
        f"/printers/{printer['id']}/services", json={"kind": "filter_change"}, headers=headers
    )
    assert from_table.status_code == 201, from_table.text

    final = card(from_table.json())
    # `lubrication` was added while the table had no row for it: the schema's
    # own default, not a refusal and not the nozzle's 300.
    assert final["lubrication"] == 500
    assert final["bed_level"] == 250
    # `filter_change` was added while the table carried no row for it either —
    # the second table names only `lubrication` — so it, too, is the schema default.
    assert final["filter_change"] == 500


async def test_the_table_row_is_what_an_omitted_periodicity_becomes(
    client: AsyncClient, database: _TestDatabase, clock: FixedClock
) -> None:
    headers = await auth(client)
    await set_table(database, clock, [{"code": "lubrication", "interval_hours": 900}])
    printer = await register(client, headers, "p1s-04")
    assert card(printer) == {"lubrication": 900}

    await set_table(
        database,
        clock,
        [
            {"code": "lubrication", "interval_hours": 900},
            {"code": "bed_level", "interval_hours": 120},
        ],
    )
    added = await client.post(
        f"/printers/{printer['id']}/services", json={"kind": "bed_level"}, headers=headers
    )

    assert added.status_code == 201, added.text
    assert card(added.json())["bed_level"] == 120
