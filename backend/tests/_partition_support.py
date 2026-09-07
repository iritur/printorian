"""A scratch database with nothing in it but the partitioned telemetry table.

Extracted when `test_partition_headroom.py` needed the same fixture and
`test_partitions.py` was thirty lines from the 400-line gate. Copying it would
have been the worse of the two mistakes here: the two files assert about the *same*
catalogue — which relations count as a partition, and which one is the `DEFAULT`
that never does — so a drifted copy would let one file go on passing about a shape
the other had already changed.

**Its own database per module, dropped and recreated around every test.** The
shared `printorian_test` cannot be used for partitions: `conftest` empties tables
with `TRUNCATE`, which leaves a created partition behind, and a leftover
`telemetry_samples_2026_03` silently swallows rows a later test expects to find in
the `DEFAULT` partition. That failure appears in whichever file happens to run
next, which is the kind nobody can reproduce.

Built directly rather than through Alembic, deliberately: these files exercise
`contexts.fleet.retention`, not the migration, and the two should be able to fail
independently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.core.config import Settings
from printorian.core.ids import new_id

#: The table these fixtures build, spelled here rather than imported from
#: `retention.TABLE`: the DDL below is the *fixture's* idea of the shape, and
#: taking the name from the code under test would let a rename pass unnoticed.
TABLE = "telemetry_samples"

_CREATE_TABLE = f"""
    CREATE TABLE {TABLE} (
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
        CONSTRAINT pk_{TABLE} PRIMARY KEY (id, created_at)
    ) PARTITION BY RANGE (created_at)
"""


def admin_engine(url: str) -> Any:
    """A sync engine that pools nothing.

    ``NullPool`` matters here rather than being tidiness: a pooled connection left
    behind by a fixture is closed whenever the garbage collector gets to it, which
    is typically in the middle of some unrelated later test — and with
    ``filterwarnings = ["error"]`` that surfaces as a failure in a file that has
    nothing to do with this one.
    """
    return create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)


def _admin_url() -> str:
    return Settings().database_url.replace("+asyncpg", "").rsplit("/", 1)[0] + "/postgres"


def postgres_reachable() -> bool:
    """Whether there is a server to build a scratch database on at all."""
    try:
        engine = admin_engine(_admin_url())
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    except Exception:
        return False
    return True


async def partition_scratch_session(database: str) -> AsyncIterator[AsyncSession]:
    """Yield a session on a freshly built scratch database, then drop it.

    The generator a module's own `db` fixture wraps. Each module passes its own
    ``database`` name so two of them can never be halfway through dropping and
    recreating the same one.
    """
    configured = Settings().database_url
    test_url = configured.rsplit("/", 1)[0] + "/" + database

    admin = admin_engine(_admin_url())
    try:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {database}"))
    finally:
        admin.dispose()

    engine = create_async_engine(test_url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(text(_CREATE_TABLE))
        await connection.execute(text(f"CREATE TABLE {TABLE}_default PARTITION OF {TABLE} DEFAULT"))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()

    admin = admin_engine(_admin_url())
    try:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)"))
    finally:
        admin.dispose()


async def partition_names(db: AsyncSession) -> set[str]:
    """Every relation attached to the partitioned table, `DEFAULT` included.

    Includes it on purpose: two of the cases that use this are about the `DEFAULT`
    partition surviving something, and a helper that hid it could not express them.
    """
    rows = await db.execute(
        text(
            f"""
            SELECT c.relname FROM pg_class c
            JOIN pg_inherits i ON i.inhrelid = c.oid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = '{TABLE}'
            """
        )
    )
    return {name for (name,) in rows}


async def insert_sample_at(db: AsyncSession, moment: datetime) -> None:
    """One telemetry row stamped at ``moment``, to see where it lands."""
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (id, created_at, printer_id, observed_at, state) "
            "VALUES (:id, :created_at, :printer_id, :observed_at, 'printing')"
        ),
        {
            "id": new_id(),
            "created_at": moment,
            "printer_id": new_id(),
            "observed_at": moment,
        },
    )
