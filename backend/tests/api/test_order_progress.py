"""What the cabinet asks for: where an order stands, and on what machine.

`GET /orders/{id}/queue` composes two contexts — `production` knows a job and a
`printer_id`, `fleet` knows what a printer is — and the composition happens in
the delivery layer because that is the only layer allowed to know both.

The cases below are the two halves being independently absent, and the ownership
rule. All three have the same failure mode if got wrong: a customer sees
something that is not theirs, or a panel that claims a machine it cannot name.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import a_shop, place, token_for


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async for http in a_shop(object_store, settings, clock, bus):
        yield http


async def test_an_order_with_no_job_reports_neither_half(client: AsyncClient) -> None:
    """Paid but nothing prepared is a real state. It answers, rather than 404s.

    Both halves null, and they mean different things: no `queue` is "no job
    exists", no `machine` is "no machine chosen". Collapsing them into one
    absence would leave the cabinet unable to tell «ещё не приняли в работу»
    from «ждём свободный принтер».
    """
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)

    response = await client.get(f"/orders/{order['id']}/queue", headers=auth)

    assert response.status_code == 200, response.text
    assert response.json() == {"queue": None, "machine": None}


async def test_another_customer_cannot_measure_the_queue(client: AsyncClient) -> None:
    """The refusal is deliberately identical to a plain permission failure.

    Queue depth is a measurement of the farm's workload, and an answer that
    differed between "not yours" and "does not exist" would also enumerate which
    orders exist.
    """
    buyer = await token_for(client, "buyer@example.com")
    rival = await token_for(client, "rival@example.com")
    order = await place(client, buyer)

    response = await client.get(f"/orders/{order['id']}/queue", headers=rival)

    assert response.status_code == 403
    assert response.json()["code"] == "error.permission_denied"


async def test_the_queue_is_closed_to_anonymous_callers(client: AsyncClient) -> None:
    buyer = await token_for(client, "buyer@example.com")
    order = await place(client, buyer)

    assert (await client.get(f"/orders/{order['id']}/queue")).status_code == 401
