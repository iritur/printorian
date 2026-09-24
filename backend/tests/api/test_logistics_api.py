"""Shipments over HTTP: the ship route opens one, the board names it, an event closes it.

Three things only the edge can prove:

* **Shipping a parcel opens its shipment**, composed in `api/routers/packaging.py`
  from the order's delivery postcode and pinned rates. A shipment that only the
  unit tests could open would leave the board empty on the real farm.
* **The gates.** Reading is `VIEW_PRODUCTION`; recording an event is `PACK_ORDER`,
  which the operator role holds and the customer does not.
* **No money on any of it**, asserted on the schemas rather than on a fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.contexts.logistics import CarrierScore, LogisticsBoard, ShipmentView, ZoneAccuracy
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.api._fleet_metrics_support import MetricsDatabase, auth, signed_in_app
from tests.unit._packaging_support import a_packer, a_parcel

_MONEY_WORDS = ("price", "cost", "total_rub", "amount", "rub", "sum", "spend", "budget", "fee")


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[MetricsDatabase]:
    database = MetricsDatabase(settings.database_url)
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
    database: MetricsDatabase,
) -> AsyncIterator[AsyncClient]:
    async with await signed_in_app(
        database, settings=settings, clock=clock, bus=bus, object_store=object_store
    ) as http:
        yield http


def _money_fields(model: type) -> set[str]:
    found: set[str] = set()
    for name, field in model.model_fields.items():  # type: ignore[attr-defined]
        if any(word in name for word in _MONEY_WORDS):
            found.add(f"{model.__name__}.{name}")
        for nested in (field.annotation, *getattr(field.annotation, "__args__", ())):
            if hasattr(nested, "model_fields") and nested is not model:
                found |= _money_fields(nested)
    return found


def test_nothing_on_the_logistics_routes_carries_money() -> None:
    for model in (LogisticsBoard, ShipmentView, CarrierScore, ZoneAccuracy):
        assert _money_fields(model) == set(), model.__name__


async def a_ready_parcel(database: MetricsDatabase, clock: FixedClock) -> tuple[str, str]:
    """An order with a parcel walked to READY through the packaging service."""
    async with database.session_factory() as session:
        packaging, parcel = await a_parcel(session, clock)
        await packaging.start(parcel.id, await a_packer(session))
        await packaging.ready(parcel.id)
        await session.commit()
        return str(parcel.order_id), str(parcel.id)


async def test_shipping_a_parcel_opens_its_shipment_and_the_board_names_the_order(
    client: AsyncClient, database: MetricsDatabase, clock: FixedClock
) -> None:
    op = await auth(client, "op@example.com")
    _order_id, parcel_id = await a_ready_parcel(database, clock)

    shipped = await client.post(f"/packaging/parcels/{parcel_id}/ship", headers=op)
    assert shipped.status_code == 200, shipped.text

    board = (await client.get("/logistics/board", headers=op)).json()
    [out] = board["in_transit"]
    assert out["order_number"].startswith("ORD-P")
    assert out["status"] == "handed_over"
    # The factory's order pinned no rates and typed no postcode: a shipment with
    # a transit time and no promise, never a promise of nought days.
    assert out["zone_code"] is None and out["promised_days"] is None
    assert [event["kind"] for event in out["events"]] == ["handed_over"]

    # Shipping again is refused by packaging, and would not have opened a
    # second shipment even if it were not — the desk is idempotent per parcel.
    again = await client.post(f"/packaging/parcels/{parcel_id}/ship", headers=op)
    assert again.status_code != 200
    assert len((await client.get("/logistics/board", headers=op)).json()["in_transit"]) == 1


async def test_an_operator_records_the_delivery_and_a_customer_may_not(
    client: AsyncClient, database: MetricsDatabase, clock: FixedClock
) -> None:
    op = await auth(client, "op@example.com")
    buyer = await auth(client, "buyer@example.com")
    _order_id, parcel_id = await a_ready_parcel(database, clock)
    await client.post(f"/packaging/parcels/{parcel_id}/ship", headers=op)
    [out] = (await client.get("/logistics/board", headers=op)).json()["in_transit"]

    refused = await client.post(
        f"/logistics/shipments/{out['id']}/events", json={"kind": "delivered"}, headers=buyer
    )
    assert refused.status_code == 403
    assert (await client.get("/logistics/board", headers=buyer)).status_code == 403

    tracked = await client.post(
        f"/logistics/shipments/{out['id']}/tracking",
        json={"tracking_number": "CDEK 1408829301"},
        headers=op,
    )
    assert tracked.status_code == 200, tracked.text
    delivered = await client.post(
        f"/logistics/shipments/{out['id']}/events",
        json={"kind": "delivered", "source": "carrier", "note": "вручено"},
        headers=op,
    )
    assert delivered.status_code == 201, delivered.text
    assert delivered.json()["status"] == "delivered"
    assert delivered.json()["tracking_number"] == "CDEK 1408829301"

    board = (await client.get("/logistics/board", headers=op)).json()
    assert board["in_transit"] == []
    assert [row["id"] for row in board["closed"]] == [out["id"]]

    scores = (await client.get("/logistics/scorecards", headers=op)).json()
    [carrier] = scores["carriers"]
    assert carrier["shipments"] == 1 and carrier["delivered"] == 1
    # No promise was pinned, so no punctuality: null, not 100%.
    assert carrier["promised"] == 0 and carrier["on_time_share"] is None
    assert scores["zones"] == []


async def test_an_unknown_shipment_is_a_404(client: AsyncClient) -> None:
    op = await auth(client, "op@example.com")
    response = await client.get(f"/logistics/shipments/{new_id()}", headers=op)
    assert response.status_code == 404
    assert response.json()["code"] == "error.logistics.shipment_not_found"
