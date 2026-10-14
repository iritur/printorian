"""The rows a successful backup or restore drill leaves behind (`settings.maintenance_runs`).

What the two ADR-0019 stamps on `/metrics` rest on:

* **the newest success of each kind**, and a kind that never succeeded is absent;
* **the shell script's insert works** — only `kind` and `detail`, through the
  server defaults, exactly the statement `scripts/backup.sh` runs through `psql`;
* **an unknown kind is refused by the database**, the contract the script is held to;
* **the drill writes its row** through the function `restore_drill.py` calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.settings import RunKind, last_successes, record_success
from printorian.contexts.settings.maintenance_runs import MaintenanceRun
from printorian.core.config import Settings
from scripts.restore_drill import _record_success


async def test_the_newest_success_of_each_kind_and_nothing_for_a_kind_never_run(
    db_session: AsyncSession,
) -> None:
    older = MaintenanceRun(kind=RunKind.BACKUP.value, detail="a.dump")
    newer = MaintenanceRun(kind=RunKind.BACKUP.value, detail="b.dump")
    db_session.add_all([older, newer])
    await db_session.flush()
    older.finished_at = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    newer.finished_at = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)
    await db_session.flush()

    stamps = await last_successes(db_session)

    assert stamps == {RunKind.BACKUP: datetime(2026, 10, 2, 3, 0, tzinfo=UTC)}
    # Never drilled: no entry, not a zero and not the epoch.
    assert RunKind.RESTORE_DRILL not in stamps


async def test_the_backup_scripts_own_insert_works_and_an_unknown_kind_is_refused(
    db_session: AsyncSession,
) -> None:
    """The statement `scripts/backup.sh` runs, verbatim apart from the file name."""
    await db_session.execute(
        text("INSERT INTO maintenance_runs (kind, detail) VALUES ('backup', 'x.dump')")
    )
    assert RunKind.BACKUP in await last_successes(db_session)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text("INSERT INTO maintenance_runs (kind) VALUES ('bogus')"))


async def test_the_drill_records_its_success_in_the_database_it_checked(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _record_success(settings.database_url, "printorian-20261014T030000Z.dump")

    rows = (
        await db_session.execute(text("SELECT kind, detail, finished_at FROM maintenance_runs"))
    ).all()
    assert [(kind, detail) for kind, detail, _ in rows] == [
        ("restore_drill", "printorian-20261014T030000Z.dump")
    ]
    assert datetime.now(UTC) - rows[0][2] < timedelta(minutes=5)


async def test_record_success_appends_rather_than_overwrites(db_session: AsyncSession) -> None:
    """The history is what somebody reads when the stamp has gone stale."""
    await record_success(db_session, RunKind.RESTORE_DRILL, detail="first.dump")
    await record_success(db_session, RunKind.RESTORE_DRILL, detail="second.dump")
    count = await db_session.scalar(text("SELECT count(*) FROM maintenance_runs"))
    assert count == 2
