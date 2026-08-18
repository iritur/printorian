"""The fleet over HTTP.

The rule under test throughout: an access code goes in and never comes back out
(ADR-0014). Everything else here is permissions.
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
ACCESS_CODE = "03d00058"


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


def a_printer(**overrides: object) -> dict[str, object]:
    return {
        "name": "p1s-01",
        "brand": "bambu",
        "serial": "20P6BJ632700731",
        "connection_mode": "lan",
        "host": "192.168.0.180",
        "access_code": ACCESS_CODE,
        "acquisition_cost": "200000",
        "expected_lifetime_hours": 20000,
        **overrides,
    }


async def register(client: AsyncClient, headers: dict[str, str], **overrides: object):
    response = await client.post("/printers", json=a_printer(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------ credentials


async def test_the_access_code_never_appears_in_any_response(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    created = await register(client, boss)

    assert created["access_code_set"] is True
    assert ACCESS_CODE not in (await client.get("/printers", headers=boss)).text
    assert ACCESS_CODE not in (await client.get(f"/printers/{created['id']}", headers=boss)).text


async def test_replacing_the_code_returns_only_that_it_is_set(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    created = await register(client, boss)

    response = await client.put(
        f"/printers/{created['id']}/access-code",
        json={"access_code": "99887766"},
        headers=boss,
    )
    assert response.status_code == 200
    assert response.json()["access_code_set"] is True
    assert "99887766" not in response.text


async def test_the_openapi_schema_exposes_no_access_code_output_field(
    client: AsyncClient,
) -> None:
    """A DTO that *could* carry the secret outward is how it eventually does."""
    schema = (await client.get("/openapi.json")).json()
    printer_view = schema["components"]["schemas"]["PrinterView"]["properties"]

    assert "access_code" not in printer_view
    assert "access_code_encrypted" not in printer_view
    assert printer_view["access_code_set"]["type"] == "boolean"


# ----------------------------------------------------- permissions


async def test_the_fleet_is_closed_to_customers(client: AsyncClient) -> None:
    buyer = await auth(client, "buyer@example.com")
    assert (await client.get("/printers", headers=buyer)).status_code == 403


async def test_operators_may_see_the_fleet_but_not_add_machines(
    client: AsyncClient,
) -> None:
    boss = await auth(client, "boss@example.com")
    await register(client, boss)

    op = await auth(client, "op@example.com")
    assert (await client.get("/printers", headers=op)).status_code == 200

    refused = await client.post("/printers", json=a_printer(name="p1s-02"), headers=op)
    assert refused.status_code == 403


async def test_an_operator_may_record_a_completed_service(client: AsyncClient) -> None:
    """The person who changed the nozzle is the person who should record it."""
    boss = await auth(client, "boss@example.com")
    created = await register(client, boss)

    with_service = await client.post(
        f"/printers/{created['id']}/services",
        json={"kind": "nozzle_change", "interval_hours": 500, "materials_used": ["nozzle-0.4"]},
        headers=boss,
    )
    assert with_service.status_code == 201
    operation_id = with_service.json()["services"][0]["id"]

    op = await auth(client, "op@example.com")
    done = await client.post(
        f"/printers/{created['id']}/services/{operation_id}/complete", headers=op
    )
    assert done.status_code == 200
    assert done.json()["services"][0]["last_done_at"] is not None


async def test_only_managers_may_change_a_credential(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    created = await register(client, boss)

    op = await auth(client, "op@example.com")
    refused = await client.put(
        f"/printers/{created['id']}/access-code", json={"access_code": "1"}, headers=op
    )
    assert refused.status_code == 403


# ---------------------------------------------------------- table


async def test_the_table_carries_state_counts_and_an_attention_total(
    client: AsyncClient,
) -> None:
    boss = await auth(client, "boss@example.com")
    await register(client, boss)

    body = (await client.get("/printers", headers=boss)).json()

    assert body["total"] == 1
    assert {entry["state"] for entry in body["counts"]} >= {"idle", "printing", "offline"}
    # A newly registered machine has never reported, so it is offline and wants
    # someone's attention rather than looking quietly healthy.
    assert body["rows"][0]["state"] == "offline"
    assert body["attention"] == 1


async def test_a_printer_reports_its_amortization(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    created = await register(client, boss)

    assert Decimal(created["amortization_per_hour"]) == Decimal("10.00")
    assert Decimal(created["printed_hours"]) == 0


# ------------------------------------------------ lot location consistency


async def test_mounting_a_lot_moves_it_in_inventory_too(client: AsyncClient) -> None:
    """The fleet owns "what is in this slot"; inventory owns "where is this spool".

    They describe one physical fact and must not be able to disagree — a materials
    table still saying "shelf A1" for a spool that is loaded in a printer sends
    someone to an empty shelf.
    """
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)

    spec = await client.post(
        "/materials",
        json={"code": "pla-test", "name": "PLA Test", "family": "PLA"},
        headers=boss,
    )
    assert spec.status_code == 201

    lot = await client.post(
        "/materials/lots",
        json={"spec_code": "pla-test", "initial_grams": 1000, "shelf": "A1"},
        headers=boss,
    )
    assert lot.status_code == 201
    lot_id = lot.json()["id"]

    mounted = await client.put(
        f"/printers/{printer['id']}/slots",
        json={"unit": 0, "index": 2, "lot_id": lot_id},
        headers=boss,
    )
    assert mounted.status_code == 200
    # The fleet half.
    assert any(s["index"] == 2 and s["lot_id"] == lot_id for s in mounted.json()["slots"])

    # The inventory half — this is what the materials table renders from.
    material = (await client.get("/materials/pla-test", headers=boss)).json()
    stored = next(candidate for candidate in material["lots"] if candidate["id"] == lot_id)

    assert stored["location_kind"] == "printer"
    assert stored["printer_id"] == printer["id"]
    assert stored["ams_unit"] == 0
    assert stored["ams_slot"] == 2
    # No longer claims to be on a shelf.
    assert stored["shelf"] is None


async def test_unmounting_returns_the_lot_to_storage(client: AsyncClient) -> None:
    """Without this a spool can enter a printer and never leave, so the materials
    table keeps showing filament in a machine it was pulled out of."""
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    await client.post(
        "/materials", json={"code": "pla-un", "name": "PLA Un", "family": "PLA"}, headers=boss
    )
    lot_id = (
        await client.post(
            "/materials/lots",
            json={"spec_code": "pla-un", "initial_grams": 1000, "shelf": "A1"},
            headers=boss,
        )
    ).json()["id"]
    await client.put(
        f"/printers/{printer['id']}/slots",
        json={"unit": 0, "index": 1, "lot_id": lot_id},
        headers=boss,
    )

    removed = await client.delete(
        f"/printers/{printer['id']}/slots/0/1", params={"shelf": "B7"}, headers=boss
    )

    assert removed.status_code == 200
    # The fleet half: the slot no longer claims to hold anything.
    assert all(slot["lot_id"] is None for slot in removed.json()["slots"])

    # The inventory half: back in stock, on the shelf it actually went to.
    material = (await client.get("/materials/pla-un", headers=boss)).json()
    lot = next(candidate for candidate in material["lots"] if candidate["id"] == lot_id)
    assert lot["location_kind"] == "stock"
    assert lot["shelf"] == "B7"
    assert lot["printer_id"] is None
    assert lot["ams_unit"] is None


async def test_unmounting_without_a_shelf_is_allowed(client: AsyncClient) -> None:
    """An operator who has not put the spool away yet still records the removal;
    "in stock, place unknown" is honest, "still loaded" is not."""
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    await client.post(
        "/materials", json={"code": "pla-un2", "name": "PLA Un2", "family": "PLA"}, headers=boss
    )
    lot_id = (
        await client.post(
            "/materials/lots", json={"spec_code": "pla-un2", "initial_grams": 500}, headers=boss
        )
    ).json()["id"]
    await client.put(
        f"/printers/{printer['id']}/slots",
        json={"unit": 0, "index": 0, "lot_id": lot_id},
        headers=boss,
    )

    removed = await client.delete(f"/printers/{printer['id']}/slots/0/0", headers=boss)
    assert removed.status_code == 200

    material = (await client.get("/materials/pla-un2", headers=boss)).json()
    lot = next(candidate for candidate in material["lots"] if candidate["id"] == lot_id)
    assert lot["location_kind"] == "stock"
    assert lot["shelf"] is None


async def test_unmounting_an_empty_slot_is_not_an_error(client: AsyncClient) -> None:
    """Recording a removal twice, or one done before the system knew, must not
    fail — there is nothing to correct and nothing was lost."""
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)

    response = await client.delete(f"/printers/{printer['id']}/slots/0/3", headers=boss)
    assert response.status_code == 200


async def test_only_inventory_managers_may_unmount(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)

    op = await auth(client, "op@example.com")
    refused = await client.delete(f"/printers/{printer['id']}/slots/0/0", headers=op)
    assert refused.status_code == 403
