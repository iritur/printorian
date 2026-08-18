"""Alembic environment.

Alembic is the *only* schema mechanism (ADR-0008). There is no hand-written
upgrader and there never will be — V1 had three and could not answer "what is the
schema" from any single place.

Every model module must be registered, or autogenerate will silently propose
dropping its tables. That list lives in `printorian.models`, which the worker
process needs for the same reason — one list, imported by both.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

# Registers every table on `Base.metadata`. Without it autogenerate sees an empty
# schema and proposes dropping the lot.
import printorian.models  # noqa: F401
from alembic import context
from printorian.core.config import get_settings
from printorian.core.db import Base, UtcDateTime

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A caller may inject a URL — the migration test does it programmatically, and the
# restore drill passes `-x url=...` so it can check a scratch copy without pointing
# the whole process at it. Otherwise use configured settings.
_injected = context.get_x_argument(as_dictionary=True).get("url")
if _injected:
    config.set_main_option("sqlalchemy.url", _injected)
elif not config.get_main_option("sqlalchemy.url", ""):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render custom column types as plain SQLAlchemy types.

    ``UtcDateTime`` is a thin TypeDecorator over ``DateTime(timezone=True)`` and
    produces identical DDL. Emitting the plain type keeps generated migrations free
    of imports from application code, so a historical migration keeps working even
    after that code is renamed.
    """
    if type_ == "type" and isinstance(obj, UtcDateTime):
        return "sa.DateTime(timezone=True)"
    return False


#: Tables that exist in the database but must never appear in the ORM metadata.
#:
#: `telemetry_samples` is declaratively partitioned, so PostgreSQL holds one child
#: table per month plus a default — `telemetry_samples_2026_08`, and so on. They are
#: real tables in the catalogue and invisible to SQLAlchemy, so autogenerate sees
#: them as tables the models forgot and proposes dropping the lot. Their lifecycle
#: belongs to `contexts.fleet.retention`, which creates and drops them by month; a
#: migration must never touch one.
_PARTITIONED_TABLE_PREFIXES = ("telemetry_samples_",)


def _include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Hide partition children from autogenerate's comparison."""
    if type_ == "table" and name is not None:
        return not name.startswith(_PARTITIONED_TABLE_PREFIXES)
    return True


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Hide anything *belonging* to a partition child — its indexes, mainly.

    `include_name` filters the tables themselves; indexes are compared against the
    reflected table they sit on, so they need their own gate or every partition's
    propagated index reads as an index the models do not declare.
    """
    table_name = getattr(getattr(obj, "table", None), "name", None) or name
    return not (table_name or "").startswith(_PARTITIONED_TABLE_PREFIXES)


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=False,
        render_item=_render_item,
        include_name=_include_name,
        include_object=_include_object,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
