"""Post-production over HTTP.

Two things are pinned here that nothing else covers:

* **who may do what** — advancing a task and passing quality control are separate
  permissions, and a customer reaches neither;
* **who the work is attributed to** — the operator is the caller, never a field
  in the request, because a task credited to somebody else makes the whole
  scorecard panel worthless.

The sweep that fills the board is covered in `test_postproduction_sweep.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.contexts.postproduction import TaskStatus
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._postproduction_support import (
    PostDatabase,
    a_paid_order,
    a_sanding_instruction,
    auth,
    seed_users,
)


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[PostDatabase]:
    database = PostDatabase(settings.database_url)
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
    database: PostDatabase,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    await seed_users(database, settings, clock)
    async with database.session_factory() as session:
        await a_sanding_instruction(session)
        await session.commit()

    app.state.settings = settings
    app.state.clock = clock
    app.state.event_bus = bus
    app.state.database = database
    app.state.object_store = object_store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http


async def a_task(
    client: AsyncClient, headers: dict[str, str], database: PostDatabase, **overrides: object
) -> dict:
    """One task, against a real order.

    `postproduction_tasks.order_id` is a real foreign key, so the order has to
    exist — which is the constraint doing its job, not test friction.
    """
    async with database.session_factory() as session:
        order = await a_paid_order(session, finishes=["sanded"])
        await session.commit()
    body = {
        "order_id": str(order.id),
        "kind": "sanding",
        "model_name": "BRACKET_V4",
        "quantity": 10,
        **overrides,
    }
    response = await client.post("/postproduction/tasks", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------- authorization


async def test_the_board_is_closed_to_anonymous_callers(client: AsyncClient) -> None:
    assert (await client.get("/postproduction/board")).status_code == 401


async def test_a_customer_reaches_none_of_it(client: AsyncClient) -> None:
    buyer = await auth(client, "buyer@example.com")

    assert (await client.get("/postproduction/board", headers=buyer)).status_code == 403
    assert (await client.post("/postproduction/tasks", json={}, headers=buyer)).status_code == 403


async def test_an_operator_may_work_the_board(client: AsyncClient) -> None:
    """The floor's own screen. Nothing on it is financial, so no manager is needed."""
    operator = await auth(client, "op@example.com")

    assert (await client.get("/postproduction/board", headers=operator)).status_code == 200


# ----------------------------------------------------------------- the shift


async def test_the_operator_is_the_caller_and_not_a_field(
    client: AsyncClient, database: PostDatabase
) -> None:
    """A task credited to somebody else makes the scorecard panel worthless."""
    operator = await auth(client, "op@example.com")
    task = await a_task(client, operator, database)

    started = await client.post(f"/postproduction/tasks/{task['id']}/start", headers=operator)

    assert started.status_code == 200, started.text
    me = (await client.get("/auth/me", headers=operator)).json()
    assert started.json()["operator_id"] == me["user_id"]


async def test_a_task_carries_its_instruction_and_its_norm(
    client: AsyncClient, database: PostDatabase
) -> None:
    """The norm-per-step is what turns a target into a gauge."""
    operator = await auth(client, "op@example.com")

    task = await a_task(client, operator, database)

    assert task["norm_minutes"] == "40.0"
    assert [step["position"] for step in task["steps"]] == [1, 2]
    assert task["steps"][1]["norm_minutes"] == "14.00"


async def test_a_step_cannot_be_ticked_before_the_task_is_picked_up(
    client: AsyncClient, database: PostDatabase
) -> None:
    operator = await auth(client, "op@example.com")
    task = await a_task(client, operator, database)

    refused = await client.post(
        f"/postproduction/tasks/{task['id']}/steps", json={"position": 1}, headers=operator
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "error.postproduction.not_in_progress"


async def test_ticking_every_step_sends_the_batch_to_inspection(
    client: AsyncClient, database: PostDatabase
) -> None:
    operator = await auth(client, "op@example.com")
    task = await a_task(client, operator, database)
    await client.post(f"/postproduction/tasks/{task['id']}/start", headers=operator)

    for position in (1, 2):
        result = await client.post(
            f"/postproduction/tasks/{task['id']}/steps",
            json={"position": position},
            headers=operator,
        )

    assert result.json()["status"] == TaskStatus.FOR_QC.value


async def test_a_return_needs_a_reason(client: AsyncClient, database: PostDatabase) -> None:
    """A rework with no recorded defect is invisible to every return-rate figure."""
    operator = await auth(client, "op@example.com")
    task = await a_task(client, operator, database)
    await client.post(f"/postproduction/tasks/{task['id']}/start", headers=operator)
    for position in (1, 2):
        await client.post(
            f"/postproduction/tasks/{task['id']}/steps",
            json={"position": position},
            headers=operator,
        )

    refused = await client.post(
        f"/postproduction/tasks/{task['id']}/return", json={}, headers=operator
    )
    assert refused.status_code == 422

    returned = await client.post(
        f"/postproduction/tasks/{task['id']}/return",
        json={"defect_code": "defect.thin_wall_broken"},
        headers=operator,
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["attempt"] == 2
    assert returned.json()["status"] == TaskStatus.RETURNED.value


# ------------------------------------------------------------------- board


async def test_an_empty_post_answers_with_every_panel_present(client: AsyncClient) -> None:
    """A shop on its first day still draws a board."""
    operator = await auth(client, "op@example.com")

    body = (await client.get("/postproduction/board", headers=operator)).json()

    assert [column["status"] for column in body["columns"]] == [
        "waiting",
        "in_progress",
        "paused",
        "curing",
        "for_qc",
        "returned",
    ]
    assert body["kpi"]["quality_percent"] is None
    assert body["kpi"]["queued"] == 0
    assert len(body["output_by_day"]) == 14


async def test_a_card_lands_in_the_column_its_status_names(
    client: AsyncClient, database: PostDatabase
) -> None:
    operator = await auth(client, "op@example.com")
    task = await a_task(client, operator, database)

    body = (await client.get("/postproduction/board", headers=operator)).json()

    waiting = next(column for column in body["columns"] if column["status"] == "waiting")
    assert [card["number"] for card in waiting["tasks"]] == [task["number"]]
