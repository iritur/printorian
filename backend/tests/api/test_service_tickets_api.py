"""«Заявки» over HTTP: the gates, the board's labels, and the refusals' codes.

Pinned here rather than in the desk's unit tests because each is a way the screen
could be wrong while every unit test passed:

* an operator may raise, work and close a ticket without any commercial
  permission, and a customer may not read the board at all;
* the board names the machine each card is about — including a retired one —
  without the client fetching `/printers`;
* a close refused for an unticked step arrives as `error.service.steps_pending`
  with the positions, and an unknown ticket is a 404 rather than an empty one;
* no field on the board or on a ticket carries money.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.contexts.service import TicketBoard, TicketView
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.api._fleet_metrics_support import MetricsDatabase, auth, register, signed_in_app

_MONEY_WORDS = ("price", "cost", "total_rub", "amount", "rub", "sum", "spend", "budget", "loss")


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


def test_neither_the_board_nor_a_ticket_carries_money() -> None:
    """On the schema, not a fixture: the kit's «Потеря 2 140 ₽» is one field away."""
    assert _money_fields(TicketBoard) == set()
    assert _money_fields(TicketView) == set()


async def test_the_board_is_closed_to_customers_and_open_to_operators(
    client: AsyncClient,
) -> None:
    assert (await client.get("/service/tickets")).status_code == 401
    buyer = await auth(client, "buyer@example.com")
    assert (await client.get("/service/tickets", headers=buyer)).status_code == 403
    op = await auth(client, "op@example.com")
    assert (await client.get("/service/tickets", headers=op)).status_code == 200


async def test_an_operator_raises_works_and_closes_a_ticket_and_the_board_labels_the_machine(
    client: AsyncClient,
) -> None:
    boss = await auth(client, "boss@example.com")
    op = await auth(client, "op@example.com")
    printer = await register(client, boss, name="P-04")

    raised = await client.post(
        "/service/tickets",
        json={
            "kind": "repair",
            "title": "чистка сопла",
            "printer_id": str(printer),
            "norm_minutes": 35,
            "steps": [{"title": "Остановить печать", "norm_minutes": 2}],
        },
        headers=op,
    )
    assert raised.status_code == 201, raised.text
    ticket = raised.json()
    assert ticket["number"].startswith("SV-")
    assert ticket["status"] == "open"
    assert ticket["origin"] == "person"

    board = (await client.get("/service/tickets", headers=op)).json()
    assert [t["id"] for t in board["board"]["emergency"]] == [ticket["id"]]
    assert {p["id"]: p["name"] for p in board["printers"]}[str(printer)] == "P-04"

    refused = await client.post(f"/service/tickets/{ticket['id']}/close", headers=op)
    assert refused.status_code == 422
    assert refused.json()["code"] == "error.service.steps_pending"
    assert refused.json()["details"]["positions"] == [1]

    ticked = await client.post(f"/service/tickets/{ticket['id']}/steps/1/done", headers=op)
    assert ticked.status_code == 200, ticked.text
    assert ticked.json()["status"] == "in_progress"
    assert ticked.json()["steps_done"] == 1

    closed = await client.post(f"/service/tickets/{ticket['id']}/close", headers=op)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_at"] is not None

    after = (await client.get("/service/tickets", headers=op)).json()["board"]
    assert after["emergency"] == [] and after["in_progress"] == []
    assert [t["id"] for t in after["closed"]] == [ticket["id"]]


async def test_a_customer_may_not_raise_a_ticket(client: AsyncClient) -> None:
    buyer = await auth(client, "buyer@example.com")
    response = await client.post(
        "/service/tickets", json={"kind": "move", "title": "перенести"}, headers=buyer
    )
    assert response.status_code == 403


async def test_an_unknown_ticket_is_a_404_not_an_empty_ticket(client: AsyncClient) -> None:
    op = await auth(client, "op@example.com")
    response = await client.get(f"/service/tickets/{new_id()}", headers=op)
    assert response.status_code == 404
    assert response.json()["code"] == "error.service.ticket_not_found"
