"""Shared fixtures.

**Every test runs against real PostgreSQL** (ADR-0021). There is no SQLite
fallback: the farm runs on one database, and a suite that ran on a different one
was quietly excusing three whole features from coverage — partitioned telemetry,
the order-number sequence, and foreign keys, which SQLite does not enforce at all.

The engine is built once per session and the schema with it; isolation between
tests is a `TRUNCATE` of every table, which is milliseconds on an empty database
and leaves `commit()` behaving exactly as it does in production. A
join-an-outer-transaction fixture would be marginally faster and would change what
`commit()` means, which is the one thing a test database must not do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Every model module, so `create_all` and `drop_all` see the whole schema.
#
# `printorian.models` rather than a list of imports maintained here. The list
# was already two contexts behind — `journal` and `account` were missing — and a
# short metadata is worse than an obviously broken one: `create_all` quietly
# builds a subset, and the once-per-process `drop_all` then fails on a foreign
# key it cannot see, in whichever module happens to run first. That is the
# `NoReferencedTableError` this module's docstring exists to prevent, and there
# is already one canonical list of tables.
import printorian.models  # noqa: F401
from printorian.core.clock import FixedClock
from printorian.core.config import Environment, Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.factories import (  # noqa: F401 - re-exported for existing imports
    ensure_lot,
    ensure_order,
    ensure_plate,
    ensure_printer,
    ensure_user,
)

FROZEN_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(FROZEN_NOW)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


#: The database the suite owns outright. Separate from the development database,
#: because every test truncates every table between cases.
TEST_DATABASE = "printorian_test"

#: Marks whether this process has rebuilt the schema yet.
#:
#: A set rather than a bool so it can be mutated without `global`, which the
#: linter discourages and which reads worse than the thing it replaces.
#:
#: `create_all` is `checkfirst` by default, so it leaves an existing table
#: alone — including one missing a column that was added to the model since.
#: The failure that produces is `UndefinedColumnError` on an INSERT, which
#: reads like a code bug and is actually a stale database. Dropping once per
#: process costs milliseconds on an empty schema and removes the whole class.
_rebuilt: set[str] = set()


def _configured_root() -> str:
    """The async URL, minus its database name."""
    return Settings().database_url.rsplit("/", 1)[0]


def test_database_url() -> str:
    return f"{_configured_root()}/{TEST_DATABASE}"


def _ensure_test_database() -> None:
    """Create the test database if it is not there yet.

    Uses a *sync* connection to the maintenance database: `CREATE DATABASE` cannot
    run inside a transaction, and `AUTOCOMMIT` is simplest to reach for here. This
    is the same approach `test_migrations.py` already takes for its own scratch
    database.
    """
    admin_url = _configured_root().replace("+asyncpg", "") + "/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DATABASE},
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def _database_ready() -> None:
    """Fail loudly, and usefully, when PostgreSQL is not running.

    Deliberately *not* a skip. A suite that silently skips itself when the database
    is absent is a suite that can report success having tested nothing — which is
    precisely the failure mode ADR-0021 removed by deleting the SQLite fallback.
    """
    try:
        _ensure_test_database()
    except OperationalError as exc:  # pragma: no cover - environment failure
        pytest.fail(
            "PostgreSQL is not reachable, and the suite has no fallback (ADR-0021).\n"
            "Start it with:  docker compose up -d postgres\n"
            f"Tried: {_configured_root()}/postgres\n"
            f"{exc}"
        )


@pytest.fixture
def settings(tmp_path: Path, _database_ready: None) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=test_database_url(),
        session_ttl_hours=12,
        storage_root=str(tmp_path / "storage"),
        # Ceilings lifted for the suite at large, and *only* there. The clock is
        # frozen in tests, so a fixed window never rolls over and a test making
        # thirty calls would be throttled by an accident of the harness rather
        # than by anything it is asserting. The tests that are about the ceilings
        # build their own `Settings` with the real numbers.
        quote_rate_per_minute=100_000,
        upload_rate_per_minute=100_000,
        auth_rate_per_minute=100_000,
        # Nothing in the suite should talk to Redis. Local events still reach
        # local WebSocket clients, which is what the socket tests assert.
        events_relay_enabled=False,
    )


@pytest.fixture
def object_store() -> InMemoryObjectStore:
    """Bytes in a dict.

    The filesystem store is exercised on its own in `test_storage.py`; everything
    else cares that a plate can be put and got back, not where it landed.
    """
    return InMemoryObjectStore()


@pytest.fixture
async def clean_database(settings: Settings) -> None:
    """The schema, present and empty.

    **Every fixture that opens its own connection must depend on this.** The API
    tests each build a `_TestDatabase` of their own, and under SQLite they were
    isolated by accident: `settings` handed out a fresh `tmp_path` file per test,
    so "the database" was a different database every time. One shared PostgreSQL
    has no such accident — a user registered in one test is still there in the
    next, and the second one fails with `error.identity.email_taken`.

    Isolation is therefore explicit now, which is the honest arrangement: the suite
    says what it needs instead of inheriting it from a quirk of the file layout.
    """
    # `NullPool`: the engine is per-test and each test has its own event loop, so
    # a pooled connection outlives the loop that created it and is finalised by
    # the garbage collector instead of being closed — which asyncpg reports as an
    # unraisable exception in whichever unrelated test happens to trigger GC.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.begin() as connection:
        if TEST_DATABASE not in _rebuilt:
            # Once per process: drop first, so a column added to a model since
            # the last run actually appears. See `_schema_rebuilt`.
            await connection.run_sync(Base.metadata.drop_all)
            _rebuilt.add(TEST_DATABASE)
        await connection.run_sync(Base.metadata.create_all)
        # `telemetry_samples` is declaratively partitioned (ADR-0018), so
        # `create_all` builds the parent and no child. An insert with nowhere to
        # land errors — which SQLite could never have told us, because it built the
        # table as an ordinary one.
        await _ensure_telemetry_partitions(connection)
        await _truncate_everything(connection)
    await engine.dispose()


@pytest.fixture
async def db_session(settings: Settings, clean_database: None) -> AsyncIterator[AsyncSession]:
    """A session on the real thing.

    The engine is per-test rather than per-session because `asyncio_mode = "auto"`
    gives each test its own event loop, and an asyncpg pool cannot be shared across
    loops.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session

    await engine.dispose()


async def _ensure_telemetry_partitions(connection: object) -> None:
    """Give the partitioned table somewhere to put rows.

    A default partition rather than the month-by-month set the maintenance worker
    creates: tests use `FixedClock` and write samples stamped in whatever month the
    case needs, and a suite whose inserts depend on today's date fails in
    production only on the first of a month.
    """
    from printorian.contexts.fleet.retention import DEFAULT_PARTITION, TABLE

    await connection.execute(  # type: ignore[attr-defined]
        text(f"CREATE TABLE IF NOT EXISTS {DEFAULT_PARTITION} PARTITION OF {TABLE} DEFAULT")
    )


async def _truncate_everything(connection: object) -> None:
    """Empty every table, in one statement.

    `TRUNCATE ... CASCADE` rather than dropping and recreating the schema: on an
    empty database it is milliseconds, and it leaves `commit()` meaning exactly
    what it means in production. `RESTART IDENTITY` resets the order-number
    sequence too, so numbering is deterministic per test.
    """
    tables = ", ".join(
        f'"{table.name}"'
        for table in Base.metadata.sorted_tables
        # The partition child is emptied through its parent; naming it as well is
        # an error.
        if table.name != "telemetry_samples"
    )
    await connection.execute(  # type: ignore[attr-defined]
        text(f"TRUNCATE {tables}, telemetry_samples RESTART IDENTITY CASCADE")
    )
    # `RESTART IDENTITY` only resets sequences *owned by* a truncated column.
    # `order_number_seq` stands alone — it is read by `nextval`, not attached to
    # a serial — so it keeps climbing and order numbers stop being predictable.
    # SQLite never ran this path at all: it reports no sequence support, and
    # `_next_number` fell back to counting rows.
    await connection.execute(  # type: ignore[attr-defined]
        text("ALTER SEQUENCE IF EXISTS order_number_seq RESTART WITH 1")
    )


@pytest.fixture
def production_settings() -> Settings:
    """Settings that claim to be production — used to prove the mock driver refuses."""
    return Settings(environment=Environment.PRODUCTION)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    from printorian.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def give_job_a_plate(
    session: AsyncSession,
    store: InMemoryObjectStore,
    clock: FixedClock,
    job_id: object,
    *,
    content: bytes = b"3mf-plate-bytes",
    filename: str = "cube.3mf",
) -> None:
    """Put a real, dispatchable plate behind a job.

    Dispatch reads plate bytes from the object store and refuses to send an empty
    file (ADR-0007), so a test that wants to reach a printer has to give the job
    something to send. This is that setup, in one place, because a dozen tests need
    it and none of them are about how a plate is recorded.
    """
    from printorian.contexts.catalog import PlateLibrary, RecordPlate
    from printorian.contexts.production.models import PrintJob

    stored = await store.put(content, suffix="3mf")
    plate = await PlateLibrary(session, clock).record(
        RecordPlate(
            model_hash=stored.digest,
            material_code="PLA",
            printer_profile="test",
            print_minutes=Decimal(120),
            filament_grams={"0": Decimal(50)},
            filename=filename,
            content_sha256=stored.digest,
            storage_path=stored.path,
            size_bytes=stored.size_bytes,
        )
    )
    job = await session.get(PrintJob, job_id)
    assert job is not None
    job.prepared_plate_id = plate.id
    job.plate_filename = filename
    await session.flush()


@pytest.fixture
def with_plate(
    db_session: AsyncSession, object_store: InMemoryObjectStore, clock: FixedClock
) -> Callable[..., Awaitable[None]]:
    """Give a job a dispatchable plate.

    Bound to the same session, store and clock the service under test uses, so a
    test only has to say *which* job needs one.
    """

    async def attach(job_id: object, **overrides: object) -> None:
        await give_job_a_plate(db_session, object_store, clock, job_id, **overrides)  # type: ignore[arg-type]

    return attach


def wire_app(
    app: object,
    *,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    database: object,
    object_store: InMemoryObjectStore,
) -> None:
    """Put on ``app.state`` everything `create_app`'s lifespan would.

    The API tests drive the app through `ASGITransport`, which does not run the
    lifespan, so each of them used to assign the five pieces of state it happened
    to need — the same five lines copied into thirteen files. Every new piece of
    state then broke all thirteen at once, which is how this helper came to exist.

    Deliberately *not* a fixture: the client fixtures build their own database per
    test and must set it here, so this is the one shape they all call.
    """
    from printorian.api.ws import Hub
    from printorian.core.cpu import CpuGate
    from printorian.core.driver_health import DriverStates
    from printorian.core.heartbeat import Heartbeat
    from printorian.core.ratelimit import Lockout, RateLimiter

    app.state.settings = settings  # type: ignore[attr-defined]
    app.state.clock = clock  # type: ignore[attr-defined]
    app.state.event_bus = bus  # type: ignore[attr-defined]
    app.state.database = database  # type: ignore[attr-defined]
    app.state.object_store = object_store  # type: ignore[attr-defined]
    app.state.cpu = CpuGate(settings.cpu_workers)  # type: ignore[attr-defined]
    app.state.limiter = RateLimiter(clock)  # type: ignore[attr-defined]
    app.state.lockout = Lockout(clock)  # type: ignore[attr-defined]
    app.state.heartbeat = Heartbeat(settings.redis_url)  # type: ignore[attr-defined]
    app.state.driver_states = DriverStates(settings.redis_url)  # type: ignore[attr-defined]
    # No relay: the suite runs without Redis, and the hub still receives every
    # event raised in this process straight off the bus.
    app.state.relay = None  # type: ignore[attr-defined]
    hub = Hub()
    hub.attach(bus)
    app.state.hub = hub  # type: ignore[attr-defined]
