"""Partition maintenance, against real PostgreSQL.

SQLite has no partitions, so — like `test_migrations.py` — this is one of the few
places that needs a real database and is skipped when none is reachable.

What is being protected here is a specific failure: partitions are not
self-maintaining, and a month with no partition is a *failed insert*. The farm
would stop recording what its printers are doing because a maintenance job did not
run, and the first symptom would be errors in the telemetry poller on the first of
the month.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.contexts.fleet import retention
from printorian.core.config import Settings
from printorian.core.ids import new_id

pytestmark = pytest.mark.db


def _admin_engine(url: str):
    """A sync engine that pools nothing.

    ``NullPool`` matters here rather than being tidiness: a pooled connection left
    behind by a fixture is closed whenever the garbage collector gets to it, which
    is typically in the middle of some unrelated later test — and with
    ``filterwarnings = ["error"]`` that surfaces as a failure in a file that has
    nothing to do with this one.
    """
    return create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)


def _reachable() -> bool:
    try:
        url = Settings().database_url.replace("+asyncpg", "").rsplit("/", 1)[0] + "/postgres"
        engine = _admin_engine(url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    except Exception:
        return False
    return True


if not _reachable():  # pragma: no cover - environment dependent
    pytest.skip("no PostgreSQL reachable — run `docker compose up -d`", allow_module_level=True)


TEST_DATABASE = "printorian_partition_test"


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A scratch database with just the partitioned table in it.

    Built directly rather than through Alembic: this exercises
    `contexts.fleet.retention`, not the migration, and the two should be able to
    fail independently.
    """
    configured = Settings().database_url
    admin_url = configured.replace("+asyncpg", "").rsplit("/", 1)[0] + "/postgres"
    test_url = configured.rsplit("/", 1)[0] + "/" + TEST_DATABASE

    admin = _admin_engine(admin_url)
    try:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {TEST_DATABASE}"))
    finally:
        admin.dispose()

    engine = create_async_engine(test_url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE telemetry_samples (
                    id UUID NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    printer_id UUID NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    state VARCHAR(40) NOT NULL,
                    job_handle VARCHAR(200),
                    progress_percent INTEGER,
                    layer_current INTEGER,
                    layer_total INTEGER,
                    remaining_minutes NUMERIC(10, 2),
                    nozzle_temp_c NUMERIC(6, 2),
                    bed_temp_c NUMERIC(6, 2),
                    error_code VARCHAR(120),
                    CONSTRAINT pk_telemetry_samples PRIMARY KEY (id, created_at)
                ) PARTITION BY RANGE (created_at)
                """
            )
        )
        await connection.execute(
            text("CREATE TABLE telemetry_samples_default PARTITION OF telemetry_samples DEFAULT")
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()

    admin = _admin_engine(admin_url)
    try:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)"))
    finally:
        admin.dispose()


async def _partitions(db: AsyncSession) -> set[str]:
    rows = await db.execute(
        text(
            """
            SELECT c.relname FROM pg_class c
            JOIN pg_inherits i ON i.inhrelid = c.oid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = 'telemetry_samples'
            """
        )
    )
    return {name for (name,) in rows}


async def _insert_at(db: AsyncSession, moment: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO telemetry_samples (id, created_at, printer_id, observed_at, state) "
            "VALUES (:id, :created_at, :printer_id, :observed_at, 'printing')"
        ),
        {
            "id": new_id(),
            "created_at": moment,
            "printer_id": new_id(),
            "observed_at": moment,
        },
    )


async def test_provisioning_creates_this_month_and_the_next_ones(db: AsyncSession) -> None:
    created = await retention.ensure_partitions(
        db, now=datetime(2026, 3, 14, tzinfo=UTC), months_ahead=2
    )

    assert created == (
        "telemetry_samples_2026_03",
        "telemetry_samples_2026_04",
        "telemetry_samples_2026_05",
    )
    assert set(created) <= await _partitions(db)


async def test_provisioning_is_idempotent(db: AsyncSession) -> None:
    """Safe to run on every sweep, and safe to run from two processes at once."""
    now = datetime(2026, 3, 14, tzinfo=UTC)
    await retention.ensure_partitions(db, now=now, months_ahead=1)
    before = await _partitions(db)

    await retention.ensure_partitions(db, now=now, months_ahead=1)

    assert await _partitions(db) == before


async def test_provisioning_rolls_over_a_year_boundary(db: AsyncSession) -> None:
    """December + 2 is February, not month 14."""
    created = await retention.ensure_partitions(
        db, now=datetime(2026, 12, 1, tzinfo=UTC), months_ahead=2
    )

    assert created == (
        "telemetry_samples_2026_12",
        "telemetry_samples_2027_01",
        "telemetry_samples_2027_02",
    )


async def test_a_sample_lands_in_its_own_month(db: AsyncSession) -> None:
    await retention.ensure_partitions(db, now=datetime(2026, 3, 1, tzinfo=UTC), months_ahead=1)

    await _insert_at(db, datetime(2026, 4, 15, 12, 0, tzinfo=UTC))

    count = await db.scalar(text("SELECT count(*) FROM ONLY telemetry_samples_2026_04"))
    assert count == 1
    assert await retention.unroutable_sample_count(db) == 0


async def test_an_unprovisioned_month_falls_into_the_default_partition(db: AsyncSession) -> None:
    """The safety net. Provisioning falling behind must not stop the farm recording.

    A row with nowhere to go would be a failed insert in the telemetry poller —
    the fleet would go blind because a cron job did not run. It lands in the
    default instead, and the maintenance sweep reports that it happened.
    """
    await retention.ensure_partitions(db, now=datetime(2026, 3, 1, tzinfo=UTC), months_ahead=0)

    await _insert_at(db, datetime(2027, 9, 9, tzinfo=UTC))

    assert await retention.unroutable_sample_count(db) == 1


async def test_retention_drops_only_fully_elapsed_months(db: AsyncSession) -> None:
    """A partition goes only when *every* row in it is past retention.

    Whole-partition granularity is what makes the drop instant, and the price is
    that data lives for the retention window plus up to a month. What must never
    happen is the reverse — dropping a partition still holding rows inside it.
    """
    for month in (1, 2, 3, 4):
        await retention.ensure_partitions(
            db, now=datetime(2026, month, 1, tzinfo=UTC), months_ahead=0
        )

    # Cutoff inside March: January and February are wholly past it, March is not.
    dropped = await retention.drop_partitions_before(db, cutoff=datetime(2026, 3, 20, tzinfo=UTC))

    assert set(dropped) == {"telemetry_samples_2026_01", "telemetry_samples_2026_02"}
    remaining = await _partitions(db)
    assert "telemetry_samples_2026_03" in remaining
    assert "telemetry_samples_2026_04" in remaining


async def test_retention_never_drops_the_default_partition(db: AsyncSession) -> None:
    """It has no month, so no cutoff can be past it — and losing it would turn a
    late maintenance run into failed inserts."""
    await retention.ensure_partitions(db, now=datetime(2026, 1, 1, tzinfo=UTC), months_ahead=0)

    await retention.drop_partitions_before(db, cutoff=datetime(2030, 1, 1, tzinfo=UTC))

    assert "telemetry_samples_default" in await _partitions(db)


async def test_dropping_a_partition_removes_its_rows(db: AsyncSession) -> None:
    await retention.ensure_partitions(db, now=datetime(2026, 1, 1, tzinfo=UTC), months_ahead=1)
    await _insert_at(db, datetime(2026, 1, 5, tzinfo=UTC))
    await _insert_at(db, datetime(2026, 2, 5, tzinfo=UTC))

    await retention.drop_partitions_before(db, cutoff=datetime(2026, 2, 1, tzinfo=UTC))

    assert await db.scalar(text("SELECT count(*) FROM telemetry_samples")) == 1


async def test_retention_is_off_when_nothing_has_elapsed(db: AsyncSession) -> None:
    await retention.ensure_partitions(db, now=datetime(2026, 3, 1, tzinfo=UTC), months_ahead=1)

    dropped = await retention.drop_partitions_before(
        db, cutoff=datetime(2026, 3, 1, tzinfo=UTC) - timedelta(days=365)
    )

    assert dropped == ()
