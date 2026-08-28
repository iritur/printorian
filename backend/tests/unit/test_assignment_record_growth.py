"""Whether the farm can tell that `assignment_records` has reached the size to act at.

ADR-0018 partitioned `telemetry_samples` and deliberately left this table alone,
saying it would be *watched* instead. Nothing was watching it: the trigger — ten
million rows or 20 GiB, from `DATABASE-REVIEW` §9 — lived in a document, and the
measurement was a thing a person had to remember to take. That is the same shape as
the WAL stall in `test_wal_archiving_check`: not an outage, a threshold crossed
while every signal stays green.

The decision is a pure function over a reading, tested directly, so these cases
drive the real comparison rather than a copy of it. The reading itself is tested
against the actual catalogue, because the half of this that can silently break is
whether `reltuples` means what the code thinks it means.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production import growth
from printorian.contexts.production.growth import (
    BYTE_TRIGGER,
    ROW_TRIGGER,
    TableSize,
    estimated_rows,
    past_partition_trigger,
    table_size,
)
from printorian.contexts.production.models import AssignmentRecord, PrintJob
from printorian.contexts.production.policies import JobStatus
from printorian.core.ids import new_id
from tests.factories import ensure_order

# ------------------------------------------------------------ the reading


def test_a_never_analysed_relation_reads_as_unknown_not_empty() -> None:
    """PostgreSQL 14+ stores `-1` for "no statistics yet", precisely so it can be
    told apart from a table that really holds nothing. Collapsing the two would
    report an unmeasured table as an empty one — root CLAUDE.md §1, in the
    flattering direction."""
    assert estimated_rows(-1.0) is None


def test_a_measured_empty_relation_reads_as_zero() -> None:
    """The other side of the same line: analysed and genuinely empty is a number."""
    assert estimated_rows(0.0) == 0


def test_the_estimate_is_truncated_to_a_row_count() -> None:
    """`reltuples` is a float and rows are not."""
    assert estimated_rows(1234.5) == 1234


# ------------------------------------------------------------ the decision


def test_a_small_table_is_not_past_the_trigger() -> None:
    assert past_partition_trigger(TableSize(rows=12_000, total_bytes=4 * 1024**2)) is False


def test_the_row_half_alone_fires() -> None:
    assert past_partition_trigger(TableSize(rows=ROW_TRIGGER, total_bytes=1024)) is True


def test_the_byte_half_alone_fires() -> None:
    """Not redundant with the row half. `assignment_records.candidates` is JSONB
    holding every machine that was considered, so a farm with wide decisions
    reaches 20 GiB on fewer rows than §6's 2–5 KB average projects."""
    assert past_partition_trigger(TableSize(rows=1_000, total_bytes=BYTE_TRIGGER)) is True


def test_the_boundary_is_inclusive() -> None:
    """An approximate trigger for work that takes weeks to schedule, so the
    argument worth not having is the one about the ten-millionth row."""
    assert past_partition_trigger(TableSize(rows=ROW_TRIGGER - 1, total_bytes=0)) is False
    assert past_partition_trigger(TableSize(rows=ROW_TRIGGER, total_bytes=0)) is True


def test_an_absent_estimate_abstains_rather_than_voting_healthy() -> None:
    """The case that decides whether an unmeasured table reads as a fine one.

    With no statistics the row half cannot answer, so the byte half — which is
    exact and never absent — decides alone. A farm past 20 GiB is reported past the
    trigger whether or not anything has ever analysed the table.
    """
    assert past_partition_trigger(TableSize(rows=None, total_bytes=BYTE_TRIGGER)) is True
    assert past_partition_trigger(TableSize(rows=None, total_bytes=1024)) is False


# ------------------------------------------------------------ against the catalogue


async def test_the_reading_tracks_what_the_table_actually_holds(db_session: AsyncSession) -> None:
    """The query half, against the real `pg_class`.

    Both figures have to move for the right reason: a wrong column name, a wrong
    schema filter or a `reltuples` that is never refreshed would each leave a check
    that reports "fine" for ever, which is worse than no check at all.
    """
    empty = await table_size(db_session, growth.TABLE)

    order_id = new_id()
    await ensure_order(db_session, order_id)
    job = PrintJob(
        order_id=order_id,
        status=JobStatus.PENDING,
        material_type="PLA",
        colors=["#FFFFFF"],
        width_mm=Decimal(40),
        depth_mm=Decimal(40),
        height_mm=Decimal(40),
        grams_required=Decimal(20),
        estimated_minutes=Decimal(60),
    )
    db_session.add(job)
    await db_session.flush()
    for _ in range(3):
        db_session.add(AssignmentRecord(job_id=job.id, candidates=[]))
    await db_session.commit()

    # `reltuples` is the planner's estimate and moves only when something analyses
    # the table. Autovacuum would get there on its own; a test cannot wait for it.
    await db_session.execute(text(f"ANALYZE {growth.TABLE}"))

    filled = await table_size(db_session, growth.TABLE)
    assert filled.rows == 3
    assert filled.total_bytes > empty.total_bytes


async def test_the_test_database_is_nowhere_near_the_trigger(db_session: AsyncSession) -> None:
    """The whole check, end to end, on a table with three rows in it."""
    assert await growth.assignment_records_need_partitioning(db_session) is False


async def test_a_missing_relation_is_an_error_rather_than_a_reassuring_zero(
    db_session: AsyncSession,
) -> None:
    """Deliberate, and the reason is in `growth.table_size`: a missing
    `assignment_records` is a broken database, and `/health/ready` turns the
    exception into `database: failed`. Answering "0 rows, 0 bytes" would report the
    broken database as a healthy one."""
    with pytest.raises(Exception, match="No row was found"):
        await table_size(db_session, "no_such_table")
