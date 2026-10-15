"""The four database-backed series on `GET /metrics` (INFRASTRUCTURE §5, issue #22).

Parsed as a scraper parses them, against rows written to the real database. The
rules each test pins:

* **an empty farm reads measured zeros**, except the variance ratio, which has
  no sample at all when nothing was recorded — the mean of nothing is not 1.0;
* **a stuck job's age is measured from its entry into the state**, and a job
  with no entering event still counts, from its last change;
* **the variance mean keeps to its window** and publishes its denominator;
* **a payment the gateway said was paid and we did not settle is counted**,
  and a settled one is not.

The `client` fixture is declared here rather than imported, for the reason
`test_health_printing_api.py` gives.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.payments.models import Payment, PaymentHold, PaymentNotification
from printorian.contexts.payments.policies import PaymentStatus
from printorian.contexts.production.models import EstimateVariance, JobEvent
from printorian.contexts.production.policies import JobStatus
from printorian.contexts.settings import RunKind, record_success
from printorian.core import metrics
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app
from tests.unit._dashboard_support import a_job, an_order_id


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


class _NoDrivers:
    async def report(self) -> list[object]:
        return []


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[_TestDatabase]:
    database = _TestDatabase(settings.database_url)
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
    database: _TestDatabase,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    wire_app(
        app, settings=settings, clock=clock, bus=bus, database=database, object_store=object_store
    )
    app.state.driver_states = _NoDrivers()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http


async def scrape(client: AsyncClient) -> dict[str, list[tuple[dict[str, str], float]]]:
    body = (await client.get("/metrics")).text
    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            out.setdefault(sample.name, []).append((dict(sample.labels), sample.value))
    return out


def ago(**delta: float) -> datetime:
    """A moment in the past by the wall clock — the clock the database's `now()` reads."""
    return datetime.now(UTC) - timedelta(**delta)


async def test_an_empty_farm_reads_measured_zeros_and_no_variance_mean(
    client: AsyncClient,
) -> None:
    series = await scrape(client)

    stuck = {labels["state"]: value for labels, value in series[metrics.JOB_STUCK_SECONDS]}
    assert stuck == {"assigned": 0.0, "dispatching": 0.0}
    assert series[metrics.ORDERS_AWAITING_PREP] == [({}, 0.0)]
    assert series[metrics.PAYMENT_NOTIFICATIONS_UNRECONCILED] == [({}, 0.0)]
    assert series[metrics.ESTIMATE_VARIANCE_SAMPLES] == [({}, 0.0)]
    # The mean of nothing is not 1.0 and not 0: no sample at all.
    assert metrics.ESTIMATE_VARIANCE_RATIO not in series


async def test_a_stuck_job_is_aged_from_its_entry_into_the_state(
    client: AsyncClient, database: _TestDatabase
) -> None:
    async for db in database.session():
        order_id = await an_order_id(db)
        dispatching = a_job(order_id, status=JobStatus.DISPATCHING, grams=Decimal(50))
        assigned = a_job(order_id, status=JobStatus.ASSIGNED, grams=Decimal(50))
        printing = a_job(order_id, status=JobStatus.PRINTING, grams=Decimal(50))
        pending = [a_job(order_id, status=JobStatus.PENDING, grams=Decimal(10)) for _ in range(2)]
        db.add_all([dispatching, assigned, printing, *pending])
        await db.flush()
        # Into DISPATCHING ten minutes ago, through an event, as the service writes it.
        event = JobEvent(
            job_id=dispatching.id, sequence=1, from_status="assigned", to_status="dispatching"
        )
        db.add(event)
        await db.flush()
        event.created_at = ago(minutes=10)
        # ASSIGNED with no event at all: its last change, five minutes ago, is the age.
        assigned.updated_at = ago(minutes=5)
        # A twelve-hour plate is not stuck and has no series.
        printing.updated_at = ago(hours=12)

    scraped = await scrape(client)
    series = {labels["state"]: value for labels, value in scraped[metrics.JOB_STUCK_SECONDS]}

    assert 590 <= series["dispatching"] <= 660
    assert 290 <= series["assigned"] <= 360
    assert "printing" not in series
    assert (await scrape(client))[metrics.ORDERS_AWAITING_PREP] == [({}, 2.0)]


async def _a_variance(
    db: AsyncSession, order_id: EntityId, *, quoted: str, prepared: str, when: datetime
) -> None:
    job = a_job(order_id, status=JobStatus.READY, grams=Decimal(10))
    db.add(job)
    await db.flush()
    variance = EstimateVariance(
        job_id=job.id,
        order_id=order_id,
        quoted_cost=Decimal(quoted),
        prepared_cost=Decimal(prepared),
        tolerance=Decimal("0.15"),
    )
    db.add(variance)
    await db.flush()
    variance.created_at = when


async def test_the_variance_mean_keeps_to_its_window_and_publishes_its_denominator(
    client: AsyncClient, database: _TestDatabase
) -> None:
    async for db in database.session():
        order_id = await an_order_id(db)
        await _a_variance(db, order_id, quoted="1000", prepared="1150", when=ago(days=2))
        await _a_variance(db, order_id, quoted="1000", prepared="900", when=ago(days=5))
        # Outside the thirty days, and quoted at nothing: neither is in the mean.
        await _a_variance(db, order_id, quoted="1000", prepared="3000", when=ago(days=45))
        await _a_variance(db, order_id, quoted="0", prepared="500", when=ago(days=1))

    series = await scrape(client)

    [(_, ratio)] = series[metrics.ESTIMATE_VARIANCE_RATIO]
    assert ratio == pytest.approx(1.025)
    assert series[metrics.ESTIMATE_VARIANCE_SAMPLES] == [({}, 2.0)]


async def test_a_payment_the_gateway_said_was_paid_and_we_did_not_settle_is_counted(
    client: AsyncClient, database: _TestDatabase
) -> None:
    async for db in database.session():
        order_id = await an_order_id(db)
        for index, status in enumerate((PaymentStatus.SUCCEEDED, PaymentStatus.PENDING)):
            payment = Payment(
                order_id=order_id,
                provider="mock",
                idempotency_key=f"metrics-{index}",
                status=status,
                amount=Decimal(1000),
                currency="RUB",
            )
            db.add(payment)
            await db.flush()
            db.add(
                PaymentNotification(
                    payment_id=payment.id,
                    provider="mock",
                    event_key=f"evt-{index}",
                    status="succeeded",
                    payload={},
                )
            )

    # The pending one is money the gateway says it took; the settled one is booked.
    assert (await scrape(client))[metrics.PAYMENT_NOTIFICATIONS_UNRECONCILED] == [({}, 1.0)]


async def test_held_money_is_counted_until_somebody_decides(
    client: AsyncClient, database: _TestDatabase
) -> None:
    """Security review §5(d). A measured 0 on a quiet farm, and a count — never a sum."""
    assert (await scrape(client))[metrics.PAYMENTS_HELD] == [({}, 0.0)]

    async for db in database.session():
        order_id = await an_order_id(db)
        for index, released in enumerate((False, True)):
            payment = Payment(
                order_id=order_id,
                provider="mock",
                idempotency_key=f"held-{index}",
                status=PaymentStatus.SUCCEEDED,
                amount=Decimal(1000),
                currency="RUB",
            )
            db.add(payment)
            await db.flush()
            db.add(
                PaymentHold(
                    payment_id=payment.id,
                    order_status="cancelled",
                    released_at=datetime.now(UTC) if released else None,
                )
            )

    # The released one has been decided; only the other is still waiting.
    assert (await scrape(client))[metrics.PAYMENTS_HELD] == [({}, 1.0)]


async def test_the_backup_stamps_are_the_newest_successes_and_absent_when_there_are_none(
    client: AsyncClient, database: _TestDatabase
) -> None:
    """ADR-0019. A job that never succeeded has no sample: "never" is not a timestamp."""
    series = await scrape(client)
    assert metrics.BACKUP_LAST_SUCCESS not in series
    assert metrics.RESTORE_DRILL_LAST_SUCCESS not in series
    assert metrics.OFFSITE_LAST_SUCCESS not in series

    async for db in database.session():
        await record_success(db, RunKind.BACKUP, detail="printorian-20261014T030000Z.dump")

    series = await scrape(client)
    [(_, stamp)] = series[metrics.BACKUP_LAST_SUCCESS]
    assert abs(stamp - datetime.now(UTC).timestamp()) < 300
    assert metrics.RESTORE_DRILL_LAST_SUCCESS not in series
    assert metrics.OFFSITE_LAST_SUCCESS not in series

    # The off-site copy's stamp is its own: the backup succeeding says nothing
    # about whether anything left the farm (ADR-0019 §3).
    async for db in database.session():
        await record_success(db, RunKind.OFFSITE, detail="restic")

    series = await scrape(client)
    [(_, offsite)] = series[metrics.OFFSITE_LAST_SUCCESS]
    assert abs(offsite - datetime.now(UTC).timestamp()) < 300
