"""«Удачных печатей» and «Повторных заказов» — counted, never stored.

Both are questions about history rather than about the catalogue entry, so they
are computed at read time from the jobs and orders that actually happened. The
rules worth pinning are the exclusions: a print still on the machine is evidence
of nothing, and an anonymous order cannot be somebody's second one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from printorian.api.app import create_app
from printorian.contexts.identity import Role
from printorian.contexts.identity.models import User
from printorian.contexts.ordering.models import Order
from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.api._catalog_support import (
    _TestDatabase,
    a_model,
    model_digest,
    sign_in,
    upload,
)
from tests.conftest import wire_app


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)

    wire_app(
        app,
        settings=settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http

    await database.dispose()


@pytest.fixture
async def editor(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> AsyncClient:
    await sign_in(client, settings, clock, bus, Role.ENGINEER)
    return client


async def a_job(
    settings: Settings,
    *,
    model_hash: str,
    status: JobStatus,
    customer: str | None = None,
) -> None:
    """One order for this geometry, and the job it produced.

    Written straight to the tables rather than driven through the services: this
    is about what the counters do with history that already exists, not about how
    a job comes into being.
    """
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        customer_id = None
        if customer is not None:
            found = await session.scalar(select(User).where(User.email == customer))
            if found is None:
                found = User(id=new_id(), email=customer, display_name=customer, password_hash="x")
                session.add(found)
                await session.flush()
            customer_id = found.id

        # The *tail* of the id: `new_id()` is time-ordered, so ids minted in the
        # same millisecond share a leading prefix and collide on the unique
        # order number.
        order = Order(id=new_id(), number=f"H-{new_id().hex[-12:]}", customer_id=customer_id)
        session.add(order)
        await session.flush()
        session.add(
            PrintJob(
                id=new_id(),
                order_id=order.id,
                model_hash=model_hash,
                status=status,
                material_type="PLA",
                colors=["#FFFFFF"],
                grams_required=Decimal(20),
                estimated_minutes=Decimal(60),
            )
        )
        await session.commit()
    await database.dispose()


async def a_published_model(editor: AsyncClient) -> str:
    """A catalogue entry, and the digest its jobs will be matched on."""
    asset = await upload(editor)
    await editor.post("/catalog", json=a_model(asset, materials=[{"code": "pla"}]))
    return await model_digest(editor, "test-bracket")


async def history_of(client: AsyncClient) -> dict[str, object]:
    body = (await client.get("/catalog/test-bracket")).json()
    return dict(body["history"])


async def test_a_model_nobody_has_printed_has_no_rates(editor: AsyncClient) -> None:
    """`None`, not `0%` — which would read as "it always fails"."""
    await a_published_model(editor)

    history = await history_of(editor)

    assert history["success_rate"] is None
    assert history["finished_prints"] == 0
    assert history["repeat_share"] is None
    assert history["orders"] == 0


async def test_unfinished_prints_count_neither_way(editor: AsyncClient, settings: Settings) -> None:
    """A job still on the machine is evidence of nothing.

    Counting it as a failure would make every model look worse the busier the
    farm is — the figure would track the queue rather than the part.
    """
    digest = await a_published_model(editor)
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED)
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED)
    await a_job(settings, model_hash=digest, status=JobStatus.FAILED)
    await a_job(settings, model_hash=digest, status=JobStatus.PRINTING)

    history = await history_of(editor)

    assert history["finished_prints"] == 3, "the printing job is excluded"
    assert float(str(history["success_rate"])) == pytest.approx(66.7, abs=0.1)


async def test_the_count_travels_with_the_percentage(
    editor: AsyncClient, settings: Settings
) -> None:
    """100% of one print is not a track record, and the screen must be able to say so."""
    digest = await a_published_model(editor)
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED)

    history = await history_of(editor)

    assert float(str(history["success_rate"])) == 100.0
    assert history["finished_prints"] == 1


async def test_repeat_share_counts_orders_beyond_the_first_per_customer(
    editor: AsyncClient, settings: Settings
) -> None:
    """Four orders from two customers means two of them were repeats."""
    digest = await a_published_model(editor)
    for email in ("one@example.com", "one@example.com", "two@example.com", "two@example.com"):
        await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED, customer=email)

    history = await history_of(editor)

    assert history["orders"] == 4
    assert float(str(history["repeat_share"])) == pytest.approx(50.0, abs=0.1)


async def test_a_single_customer_ordering_once_is_no_repeat_business(
    editor: AsyncClient, settings: Settings
) -> None:
    digest = await a_published_model(editor)
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED, customer="a@example.com")

    assert float(str((await history_of(editor))["repeat_share"])) == 0.0


async def test_anonymous_orders_are_not_repeat_business(
    editor: AsyncClient, settings: Settings
) -> None:
    """An order with nobody attached cannot be somebody's second one.

    Counting it as a new buyer would push the repeat share down every time a guest
    checked out.
    """
    digest = await a_published_model(editor)
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED, customer="who@example.com")
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED, customer="who@example.com")
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED)

    history = await history_of(editor)

    assert history["orders"] == 2, "the anonymous order is excluded"
    assert float(str(history["repeat_share"])) == pytest.approx(50.0, abs=0.1)


async def test_another_models_prints_are_not_counted(
    editor: AsyncClient, settings: Settings
) -> None:
    """Matched on the mesh digest, so a different part cannot inflate this one."""
    digest = await a_published_model(editor)
    await a_job(settings, model_hash=digest, status=JobStatus.SUCCEEDED)
    await a_job(settings, model_hash="a" * 64, status=JobStatus.FAILED)

    history = await history_of(editor)

    assert history["finished_prints"] == 1
    assert float(str(history["success_rate"])) == 100.0
