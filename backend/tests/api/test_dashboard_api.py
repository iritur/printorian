"""The farm summary over HTTP.

Two things are being pinned here. The first is the authorization boundary: the
dashboard carries revenue, spend and margin beside the machine states, so seeing
production is not enough to see it — an operator who may walk the fleet screen is
not thereby entitled to the farm's finances.

The second is that it is **one** request. Every panel on the screen is read
against a single instant, and a client that had to fan out to nine endpoints would
be showing tiles that disagree with the status wall by a few seconds — on a screen
whose whole job is "what is happening right now", that is worse than being a
moment slower.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

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
            ("boss@example.com", Role.OWNER),
            ("op@example.com", Role.OPERATOR),
            ("buyer@example.com", Role.CUSTOMER),
        ):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        await session.commit()

    app.state.settings = settings
    app.state.clock = clock
    app.state.event_bus = bus
    app.state.database = database
    app.state.object_store = object_store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http

    await database.dispose()


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


# ------------------------------------------------------------- authorization


async def test_the_dashboard_is_closed_to_anonymous_callers(client: AsyncClient) -> None:
    assert (await client.get("/dashboard")).status_code == 401


async def test_a_customer_cannot_read_the_farms_finances(client: AsyncClient) -> None:
    """The storefront's own cabinet is where a customer reads their orders."""
    buyer = await auth(client, "buyer@example.com")

    assert (await client.get("/dashboard", headers=buyer)).status_code == 403


async def test_seeing_the_fleet_does_not_entitle_you_to_the_margin(
    client: AsyncClient,
) -> None:
    """An operator walks the fleet screen; this one is the owner's view.

    Both screens draw the same machines, which is exactly why the permission has
    to be checked on the server: hiding a tile in the client would leave the
    figures one request away.
    """
    operator = await auth(client, "op@example.com")

    assert (await client.get("/printers", headers=operator)).status_code == 200
    assert (await client.get("/dashboard", headers=operator)).status_code == 403


# -------------------------------------------------------------------- shape


async def test_an_empty_farm_answers_with_every_panel_present(client: AsyncClient) -> None:
    """A farm on its first day still draws a dashboard.

    Every panel is present and empty rather than absent, so the client never has
    to distinguish "no data" from "this deployment does not have that panel" —
    and a screen that renders fully on day one is one fewer special case in the
    console.
    """
    boss = await auth(client, "boss@example.com")

    response = await client.get("/dashboard", headers=boss)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "at",
        "window",
        "orders",
        "finance",
        "fleet",
        "schedule",
        "filament",
        "alerts",
        "wait_list",
    }
    assert body["fleet"]["total"] == 0
    assert body["fleet"]["throughput"]["success_percent"] is None
    assert body["filament"] == []
    assert len(body["finance"]["revenue_by_day"]) == 30


async def test_the_period_moves_the_window_and_its_comparison(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")

    body = (await client.get("/dashboard?period=month", headers=boss)).json()

    assert body["window"]["period"] == "month"
    assert body["window"]["start"] < body["window"]["end"]
    assert body["window"]["previous_start"] < body["window"]["start"]


async def test_an_unknown_period_is_refused_rather_than_guessed(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")

    assert (await client.get("/dashboard?period=fortnight", headers=boss)).status_code == 422


# --------------------------------------------------------------- status wall


async def test_the_wall_groups_machines_by_the_zone_they_stand_in(
    client: AsyncClient,
) -> None:
    """Position on the wall is supposed to say *where*.

    A machine with no recorded location still appears — in an unnamed zone at the
    end — because a printer missing from the wall entirely is the one nobody walks
    to.
    """
    boss = await auth(client, "boss@example.com")
    for name, location in (("P-01", "ЦЕХ A"), ("P-02", "ЦЕХ A"), ("P-03", "ЦЕХ B"), ("P-04", None)):
        created = await client.post(
            "/printers",
            json={"name": name, "connection_mode": "manual", "location": location},
            headers=boss,
        )
        assert created.status_code == 201, created.text

    body = (await client.get("/dashboard", headers=boss)).json()

    zones = body["fleet"]["zones"]
    assert [zone["name"] for zone in zones] == ["ЦЕХ A", "ЦЕХ B", ""]
    assert [len(zone["nodes"]) for zone in zones] == [2, 1, 1]
    assert body["fleet"]["total"] == 4


async def test_an_offline_machine_raises_an_alert_nobody_has_to_close(
    client: AsyncClient,
) -> None:
    """Alerts are derived, not stored.

    A machine that came back should stop being an alert because it is online, not
    because somebody remembered to acknowledge a row — so there is no
    acknowledgement to forget.
    """
    boss = await auth(client, "boss@example.com")
    created = await client.post(
        "/printers",
        json={"name": "P-01", "connection_mode": "lan", "host": "10.0.0.9"},
        headers=boss,
    )
    assert created.status_code == 201, created.text

    body = (await client.get("/dashboard", headers=boss)).json()

    alerts = [alert for alert in body["alerts"] if alert["subject"] == "P-01"]
    assert alerts, body["alerts"]
    # A code, never a sentence (ADR-0012).
    assert alerts[0]["code"].startswith("dashboard.alert.")
    assert alerts[0]["detail"]["state"] == "offline"


async def test_utilisation_is_printing_machines_and_not_reachable_ones(
    client: AsyncClient,
) -> None:
    """The most easily inflated figure on the screen.

    A farm of idle machines that are all online is 0% utilised, not 100% healthy.
    """
    boss = await auth(client, "boss@example.com")
    for name in ("P-01", "P-02"):
        await client.post(
            "/printers", json={"name": name, "connection_mode": "manual"}, headers=boss
        )

    body = (await client.get("/dashboard", headers=boss)).json()

    assert Decimal(body["fleet"]["utilisation_percent"]) == Decimal(0)
    assert body["fleet"]["printing"] == 0
