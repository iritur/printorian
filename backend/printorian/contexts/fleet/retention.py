"""Keeping the telemetry table's partitions provisioned, and dropping old ones.

A partitioned table is not self-maintaining: PostgreSQL will not invent next
month's partition when the clock rolls over, and a row with nowhere to go is an
error, not a warning. So two jobs have to run forever — one ahead of the data,
one behind it — and this is both of them.

**Ahead:** :func:`ensure_partitions` creates the coming months before anything
needs them. It runs far more often than it needs to (every maintenance sweep), and
is a no-op every time but one, which is the correct shape for a job whose failure
mode is "the farm cannot record what its printers are doing".

**Behind:** :func:`drop_partitions_before` removes whole months. Dropping a
partition is a catalogue operation — instant, regardless of how many rows are in
it. The alternative, ``DELETE FROM telemetry_samples WHERE created_at < ...``,
would take hours on the volumes this table reaches, hold locks the whole time, and
leave behind bloat that only ``VACUUM FULL`` reclaims. That difference is the
practical reason the table is partitioned at all.

There is also a ``DEFAULT`` partition, as a safety net: if provisioning ever falls
behind, telemetry lands there instead of erroring. It is meant to stay empty, and
:func:`unroutable_sample_count` is how a health check notices that it has not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The partitioned table these functions maintain.
TABLE = "telemetry_samples"

#: Rows that could not be routed to a month land here rather than failing the
#: insert. Anything in it means :func:`ensure_partitions` stopped running.
DEFAULT_PARTITION = f"{TABLE}_default"


@dataclass(frozen=True, slots=True)
class PartitionSweep:
    """What one maintenance pass did, for logging and the health endpoint."""

    created: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()


def partition_name(moment: datetime) -> str:
    return f"{TABLE}_{moment.year:04d}_{moment.month:02d}"


def _month_start(moment: datetime) -> datetime:
    return datetime(moment.year, moment.month, 1, tzinfo=UTC)


_DECEMBER = 12


def _next_month(moment: datetime) -> datetime:
    return (
        datetime(moment.year + 1, 1, 1, tzinfo=UTC)
        if moment.month == _DECEMBER
        else datetime(moment.year, moment.month + 1, 1, tzinfo=UTC)
    )


def _is_partitioned(db: AsyncSession) -> bool:
    """Whether this dialect has partitions at all.

    SQLite — the fast test dialect, never production (D1) — builds
    ``telemetry_samples`` as one ordinary table. Everything here is then a no-op
    rather than an error, so the maintenance worker can run unchanged in tests.
    """
    return db.get_bind().dialect.name == "postgresql"


async def ensure_partitions(
    db: AsyncSession, *, now: datetime, months_ahead: int = 2
) -> tuple[str, ...]:
    """Create this month's partition and the next ``months_ahead``.

    Idempotent via ``IF NOT EXISTS``, so it is safe to call on every sweep and safe
    to call from two processes at once.
    """
    if not _is_partitioned(db):
        return ()

    created: list[str] = []
    start = _month_start(now)
    for _ in range(months_ahead + 1):
        end = _next_month(start)
        name = partition_name(start)
        # Bounds are [start, end) — PostgreSQL range partitions are half-open, so
        # consecutive months meet exactly and no timestamp falls between two.
        await db.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {TABLE} "
                f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
            )
        )
        created.append(name)
        start = end
    return tuple(created)


async def drop_partitions_before(db: AsyncSession, *, cutoff: datetime) -> tuple[str, ...]:
    """Drop every partition whose whole range predates ``cutoff``.

    Whole-partition granularity, deliberately: a partition is dropped only when
    every row in it is past retention, so this can never remove a sample that is
    still inside the window. The practical effect is that data lives for the
    retention period *plus* up to a month, which is the price of the instant drop.

    **This is destructive and irreversible.** It is driven by
    ``telemetry_retention_days``, and it must not run before rollups exist — the
    dashboard and phase-6 P&L are supposed to read summarised history, and dropping
    raw samples with nothing summarising them first destroys the only copy. The
    default retention is set wide enough that this does not bite before then.
    """
    if not _is_partitioned(db):
        return ()

    boundary = _month_start(cutoff)
    rows = await db.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_inherits i ON i.inhrelid = c.oid
            JOIN pg_class parent ON parent.oid = i.inhparent
            WHERE parent.relname = :table AND c.relname <> :default_name
            """
        ),
        {"table": TABLE, "default_name": DEFAULT_PARTITION},
    )

    dropped: list[str] = []
    for (name,) in rows:
        month = _parse_partition_month(name)
        # `< boundary`, not `<=`: the partition holding the cutoff month still has
        # rows inside the retention window.
        if month is not None and _next_month(month) <= boundary:
            await db.execute(text(f"DROP TABLE IF EXISTS {name}"))
            dropped.append(name)
    return tuple(dropped)


async def unroutable_sample_count(db: AsyncSession) -> int:
    """Rows that landed in the default partition.

    Always zero when provisioning is healthy. Anything else means
    :func:`ensure_partitions` has not run recently enough, and is worth an alert
    rather than a log line — telemetry is still being recorded, but in a partition
    that retention cannot drop and queries cannot prune.
    """
    if not _is_partitioned(db):
        return 0
    count = await db.scalar(text(f"SELECT count(*) FROM ONLY {DEFAULT_PARTITION}"))
    return int(count or 0)


def _parse_partition_month(name: str) -> datetime | None:
    suffix = name.removeprefix(f"{TABLE}_")
    year, _, month = suffix.partition("_")
    if not (year.isdigit() and month.isdigit()):
        return None
    return datetime(int(year), int(month), 1, tzinfo=UTC)


__all__ = [
    "DEFAULT_PARTITION",
    "TABLE",
    "PartitionSweep",
    "drop_partitions_before",
    "ensure_partitions",
    "partition_name",
    "unroutable_sample_count",
]
