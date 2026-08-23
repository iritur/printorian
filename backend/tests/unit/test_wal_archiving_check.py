"""Whether the farm can tell that its backup guarantee has stopped holding.

Reproduced on the first farm host: filling the backup disk broke WAL archiving,
`pg_wal` began growing toward the data disk, and every signal stayed green —
`/health/ready` answered 200, `systemctl --failed` listed nothing, and the only
evidence was a counter nobody reads. ADR-0019's recovery point silently went from
"about a minute" to "last night's dump".

Worse, it did not clear when the disk was freed. `archive_command` copies straight
to the final name, so the part-written segment stayed there, `test ! -f` saw it,
and archiving stayed wedged until a person deleted the file by hand.

`pg_stat_archiver` is a server-wide singleton, so the interesting states cannot be
arranged from a test. The comparison is therefore a pure function over the two
watermarks and is tested directly — not mirrored in the test, which would go on
passing after the real one changed.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.core.db import archiving_stalled_on, wal_archiving_stalled

SEG_A = "00000001000000000000000A"
SEG_B = "00000001000000000000000B"
SEG_10 = "000000010000000000000010"
SEG_15 = "000000010000000000000015"


async def test_a_real_database_that_is_keeping_up_is_not_stalled(
    db_session: AsyncSession,
) -> None:
    """The query half, against the actual `pg_stat_archiver`."""
    assert await wal_archiving_stalled(db_session) is None


def test_never_failed_is_healthy() -> None:
    assert archiving_stalled_on(SEG_A, None) is None


def test_a_failure_older_than_the_last_success_is_healthy() -> None:
    """The case that argues against using `failed_count`.

    Archiving broke, recovered, and moved on. The counter would still read
    non-zero for the life of the server, so a farm that had one bad night in
    March would look broken in December — and a check that is permanently red is
    one people learn to ignore.
    """
    assert archiving_stalled_on(SEG_15, SEG_10) is None


def test_a_failure_beyond_the_last_success_is_stalled() -> None:
    """The live condition: the most recent attempt is the one that failed."""
    assert archiving_stalled_on("00000001000000000000000F", SEG_10) == SEG_10


def test_a_failure_on_the_same_segment_as_the_last_success_is_stalled() -> None:
    """The boundary, and it must be inclusive.

    Postgres records the success, then a later attempt at the *same* segment
    fails — retrying is exactly what it does. Treating equal as healthy would
    blind the check to the first tick of every stall.
    """
    assert archiving_stalled_on(SEG_10, SEG_10) == SEG_10


def test_failing_before_anything_was_ever_archived_is_stalled() -> None:
    """A farm whose archiving has never once worked.

    Precisely the state `deploy/compose.prod.yml` records from the development
    stack — 1 385 failed archives, zero successes — where a null high-water mark
    must not read as "nothing has gone wrong yet".
    """
    assert archiving_stalled_on(None, "000000010000000000000001") == "000000010000000000000001"


def test_nothing_has_happened_at_all_is_healthy() -> None:
    """A freshly started server, before it has archived or failed anything."""
    assert archiving_stalled_on(None, None) is None


@pytest.mark.parametrize(("archived", "failed"), [(SEG_A, SEG_B), (SEG_10, SEG_15)])
def test_segment_names_order_lexically(archived: str, failed: str) -> None:
    """WAL names are fixed-width uppercase hex, so text order is real order.

    Worth pinning: the comparison inverts silently if names are ever lowercased
    or trimmed, and `...000A` against `...000B` is the pair that would still look
    correct in a casual reading.
    """
    assert archiving_stalled_on(archived, failed) == failed
    assert archiving_stalled_on(failed, archived) is None
