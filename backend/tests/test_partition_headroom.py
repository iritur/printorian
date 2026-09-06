"""How much partition headroom the farm has, against real PostgreSQL.

`printorian_telemetry_partition_months_ahead` is the gauge INFRASTRUCTURE §5 alerts
on below 1, and it is the only warning the farm gets before a month arrives with no
partition to write telemetry into. The reading has to come from the catalogue, so
this needs a real database (ADR-0021) and a scratch one of its own — see
`tests/_partition_support.py` for why it cannot borrow `printorian_test`.

The cases below are chosen around the one mistake that would make the gauge
useless: **not counting is not the same as counting zero.** A farm with only the
current month provisioned must publish `0` — that is the alert firing — while a
database that was never asked publishes nothing at all. A helper that quietly
returned `0` for both would satisfy the second half and silently disarm the first.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import retention
from tests._partition_support import (
    partition_names,
    partition_scratch_session,
    postgres_reachable,
)

pytestmark = pytest.mark.db

if not postgres_reachable():  # pragma: no cover - environment dependent
    pytest.skip("no PostgreSQL reachable — run `docker compose up -d`", allow_module_level=True)


TEST_DATABASE = "printorian_headroom_test"

NOW = datetime(2026, 3, 14, tzinfo=UTC)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A scratch database with just the partitioned table in it."""
    async for session in partition_scratch_session(TEST_DATABASE):
        yield session


async def test_headroom_counts_the_months_provisioning_ran_ahead(db: AsyncSession) -> None:
    """`months_ahead=2` provisions this month and two more, so the answer is 2.

    Deliberately read back through `ensure_partitions` rather than by creating the
    tables by hand: what the alert has to track is what the maintenance sweep
    actually did, and a test that arranged the partitions itself would go on
    passing after the sweep stopped creating them.
    """
    await retention.ensure_partitions(db, now=NOW, months_ahead=2)

    assert await retention.months_provisioned_ahead(db, now=NOW) == 2


async def test_only_this_month_provisioned_is_a_measured_zero(db: AsyncSession) -> None:
    """The alerting case, and the reading that must not be confused with absence.

    Zero months ahead means the farm has until the first of next month. It is a
    real measurement and is exported as `0`; `None` is reserved for a database
    that could not be asked, and the exporter drops that series entirely rather
    than publishing a zero nobody measured (root CLAUDE.md §1).
    """
    await retention.ensure_partitions(db, now=NOW, months_ahead=0)

    assert await retention.months_provisioned_ahead(db, now=NOW) == 0


async def test_the_default_partition_is_never_counted_as_headroom(db: AsyncSession) -> None:
    """It is a safety net, not a month — and counting it would invert the signal.

    Rows land in `telemetry_samples_default` precisely when provisioning has
    already failed. A gauge that scored it as a month of headroom would go quiet
    at the exact moment the farm needed it to speak.
    """
    assert "telemetry_samples_default" in await partition_names(db)

    assert await retention.months_provisioned_ahead(db, now=NOW) == 0


async def test_months_already_gone_are_not_headroom(db: AsyncSession) -> None:
    """January's partition is history, not room to write into.

    Counting every partition rather than only the future ones is the easy version
    of this reading, and it would report a farm that has been running a year as
    comfortably provisioned on the day the sweep stopped.
    """
    for month in (1, 2, 3):
        await retention.ensure_partitions(
            db, now=datetime(2026, month, 1, tzinfo=UTC), months_ahead=0
        )

    assert await retention.months_provisioned_ahead(db, now=NOW) == 0


async def test_the_current_month_itself_is_not_headroom(db: AsyncSession) -> None:
    """Boundary, and the off-by-one that would hide the alert.

    March is provisioned and April is not. If the current month counted, this
    would read `1` — "a month in hand" — on a farm that has none, and the gauge
    would never cross below the trigger.
    """
    await retention.ensure_partitions(db, now=NOW, months_ahead=1)

    assert "telemetry_samples_2026_03" in await partition_names(db)
    assert "telemetry_samples_2026_04" in await partition_names(db)
    assert await retention.months_provisioned_ahead(db, now=NOW) == 1
