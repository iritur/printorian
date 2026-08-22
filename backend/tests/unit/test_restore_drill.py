"""The verdict the nightly restore drill reaches.

ADR-0019 and ARCHITECTURE §10 both say untested backups are not backups. The drill
is what makes that true, so the drill's own arithmetic is worth proving — and it
has already been wrong once in the direction that matters least visibly: it failed
on farms that had simply never taken a payment, which would have had it switched
off long before it ever caught a real failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.restore_drill import COMPARED, compare


def counts(users: int = 12, orders: int = 11, payments: int = 0) -> dict[str, int]:
    return {"users": users, "orders": orders, "payment_notifications": payments}


def test_a_faithful_restore_passes() -> None:
    lines = compare(counts(), counts())

    assert len(lines) == len(COMPARED)


def test_a_farm_that_has_taken_no_payments_still_passes() -> None:
    """The regression this function was rewritten for.

    Every farm looks like this in its first week, which is exactly when the first
    drills run. Failing here trains an operator to ignore the drill before it has
    ever told them anything true.
    """
    lines = compare(counts(payments=0), counts(payments=0))

    assert "nothing to restore yet" in lines[-1]


@pytest.mark.parametrize("table", COMPARED)
def test_a_table_the_live_database_has_and_the_dump_does_not_fails(table: str) -> None:
    """The failure the drill exists to catch.

    A backup pointed at the wrong database name produces a dump that restores
    perfectly and contains nothing. Every other check in the drill passes on it.
    """
    live = counts(payments=40)
    restored = {**live, table: 0}

    with pytest.raises(RuntimeError, match=r"wrong database|has an owner"):
        compare(restored, live)


def test_the_message_names_the_table_and_both_counts() -> None:
    """Read at 3am by somebody who did not write it."""
    with pytest.raises(RuntimeError) as failure:
        compare(counts(orders=0), counts(orders=11))

    assert "'orders'" in str(failure.value)
    assert "11 rows" in str(failure.value)


def test_an_empty_users_table_fails_even_when_the_source_is_empty_too() -> None:
    """The one table that does not get the benefit of the comparison.

    A farm always has an owner — `tools/provision_owner.py` is the first thing run
    against it — so `users` empty on *both* sides means the drill is comparing two
    wrong databases rather than finding two matching right ones.
    """
    with pytest.raises(RuntimeError, match="always has an owner"):
        compare(counts(users=0, orders=0), counts(users=0, orders=0))


def test_more_rows_in_the_restore_than_live_is_not_a_failure() -> None:
    """The dump is older than the live database, never newer — except after a
    delete. Neither is the drill's business: it checks that the backup captured
    something, not that the farm has stopped changing.
    """
    compare(counts(orders=11), counts(orders=4))
