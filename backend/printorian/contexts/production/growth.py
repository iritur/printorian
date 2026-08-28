"""How big ``assignment_records`` has got, and whether that is big enough to act on.

ADR-0018 partitioned ``telemetry_samples`` from its first row and deliberately did
not partition this table: it is two orders of magnitude smaller, and its growth is
bounded by planning frequency rather than by the clock — a farm that plans rarely
writes rarely, whereas telemetry arrives whether anything happens or not. So the
ADR says it is *watched* rather than pre-split, and `DATABASE-REVIEW` §9 repeats
the claim.

Nothing was watching it. The trigger — **10 million rows or 20 GiB** — was a number
in a document that a person had to remember to go and look at, and "watched" is a
strong word for that. This module is what makes the claim true: the reading is
taken on every readiness probe and reported as `assignment_records` in
``/health/ready``, the same treatment `telemetry_partitions` gets and for the same
reason. The failure it guards against is not an outage; it is a threshold crossed
in silence while every signal stays green.

**Both halves come from the catalogue, not from the table**, because this runs on a
probe a container runtime calls every few seconds. ``count(*)`` over ten million
rows would be a sequential scan on the readiness path, which would make the check
itself the operational problem. ``pg_total_relation_size`` is exact and free;
``reltuples`` is the planner's estimate and is free.

**The estimate can be absent, and absent is not zero** (root CLAUDE.md §1).
PostgreSQL stores ``reltuples = -1`` for a relation it has never analysed, so
:class:`TableSize` carries ``rows=None`` there rather than a confident ``0``. The
byte half is never absent, and at the 2–5 KB per row `DATABASE-REVIEW` §6 measures,
20 GiB arrives at or before 10 million rows — so the trigger still fires on a farm
whose statistics are missing. That is why the two halves are OR'd and why the
missing one is allowed to abstain instead of voting "fine".

**This check does not clear by itself.** Once the table is over the line it stays
degraded until the table is partitioned, which is the intent: it reports a
threshold that is crossed once, not a fault that comes and goes. That is the
opposite of `core.db.wal_archiving_stalled`, which deliberately compares watermarks
rather than reading a counter so that one bad night in March does not show red in
December — worth naming the difference, because the reasoning there does not
transfer here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The table this module watches. Unpartitioned, by the decision in ADR-0018.
TABLE = "assignment_records"

#: Rows at which ADR-0018's pattern should be applied to :data:`TABLE`, from
#: `DATABASE-REVIEW` §9. Roughly seven months of a fifty-printer farm at the ~17M
#: rows a year §6 projects, which is the point: it must fire with time left to do
#: the work, because converting a large table to a partitioned one means copying
#: it with writes stopped.
ROW_TRIGGER = 10_000_000

#: The other half of the same trigger, in bytes. "20 GB" in the review, read as
#: GiB — the difference is 7% of a number that is itself approximate, and every
#: other size in this system is binary.
BYTE_TRIGGER = 20 * 1024**3


@dataclass(frozen=True, slots=True)
class TableSize:
    """A table's size as the catalogue reports it.

    ``rows`` is the planner's estimate and is ``None`` when PostgreSQL has never
    analysed the relation — genuinely unknown, not zero. ``total_bytes`` includes
    indexes and TOAST, because those are what fill the disk alongside the heap and
    this table's ``candidates`` column is JSONB large enough to be TOASTed.
    """

    rows: int | None
    total_bytes: int


async def table_size(db: AsyncSession, table: str) -> TableSize:
    """Read one relation's size out of the catalogue.

    Constant time whatever the table holds: both figures are columns of
    ``pg_class``, so this touches no page of the table itself.

    Raises if ``table`` is not a relation in the current schema. That is
    deliberate — a missing ``assignment_records`` is a broken database, and the
    caller in ``/health/ready`` already turns an exception here into
    ``database: failed``, which is the truthful report of that state.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT c.reltuples, pg_total_relation_size(c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = :table AND n.nspname = current_schema()
                """
            ),
            {"table": table},
        )
    ).one()
    return TableSize(rows=estimated_rows(float(row[0])), total_bytes=int(row[1]))


def estimated_rows(reltuples: float) -> int | None:
    """``pg_class.reltuples`` as a row count, or ``None`` for "never analysed".

    PostgreSQL 14 and later store ``-1`` rather than ``0`` for a relation that has
    not been analysed since it was created, precisely so the two states can be told
    apart. Collapsing them would report an unmeasured table as an empty one, which
    is the flattering direction and the one root CLAUDE.md §1 is about.
    """
    return None if reltuples < 0 else int(reltuples)


def past_partition_trigger(size: TableSize) -> bool:
    """Whether a reading has reached the size ADR-0018 said to act at.

    Either half is sufficient — they are two readings of one condition, not two
    conditions — and an absent row estimate abstains rather than answering "no",
    which is what leaves the byte half deciding on a farm with no statistics.
    """
    over_rows = size.rows is not None and size.rows >= ROW_TRIGGER
    return over_rows or size.total_bytes >= BYTE_TRIGGER


async def assignment_records_need_partitioning(db: AsyncSession) -> bool:
    """The whole check, as ``/health/ready`` calls it."""
    return past_partition_trigger(await table_size(db, TABLE))


__all__ = [
    "BYTE_TRIGGER",
    "ROW_TRIGGER",
    "TABLE",
    "TableSize",
    "assignment_records_need_partitioning",
    "estimated_rows",
    "past_partition_trigger",
    "table_size",
]
