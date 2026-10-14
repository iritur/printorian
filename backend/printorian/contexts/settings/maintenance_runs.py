"""When the backup and the restore drill last *succeeded*, kept where the scrape can read it.

INFRASTRUCTURE §5's `printorian_backup_last_success_timestamp` and
`printorian_restore_drill_last_success_timestamp`, and the off-site copy's
`printorian_offsite_last_success_timestamp` beside them. The first two could not
be served, because neither `scripts/backup.sh` nor `scripts/restore_drill.py`
left anything machine-readable behind: a backup that had stopped succeeding looked, from
outside, exactly like one that was fine — the disk-full incident ADR-0019 was
written after.

**A row per success, written by the job itself, and only on success.** Not a
status column flipped to "failed": a job that dies before it can write anything
— the disk full, the container gone, the timer never firing — is precisely the
failure this exists to catch, and it cannot be relied on to report itself. So
the signal is *staleness*: the newest success getting old. The alert is
"`absent(x) or time() - x > 26h`", and the `absent` half matters, because a farm
that has never backed up has no row and therefore no sample at all (ADR-0007 —
"never" is not a timestamp).

**In the database, not in a marker file**, because the jobs run in different
containers: the backup in `postgres`, which owns the backup volume, the drill in
`api`, which mounts that volume read-only on purpose (`deploy/compose.prod.yml`),
and the off-site copy in a restic container that has no database client at all,
so its unit writes the row through `postgres` once the copy succeeded. One table
every job can write is one mechanism; a file for one and something else for the
others would be several that could disagree.

It lives in `settings` because «Обслуживание системы» is the section that owns
these jobs — `maintenance.backup_hour` and the irreversible operations sit there.
Append-only: a few rows a week, and the history is what a person reads when the
stamp has gone stale and they want to know since when.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, String, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from printorian.core.db import Entity, UtcDateTime
from printorian.core.ids import EntityId, new_id


class RunKind(StrEnum):
    BACKUP = "backup"
    RESTORE_DRILL = "restore_drill"
    #: The copy to the restic repository off the farm (`deploy/offsite.sh`),
    #: written by `printorian-offsite.service` once the copy has completed.
    OFFSITE = "offsite"


class MaintenanceRun(Entity):
    """One successful run of a scheduled maintenance job."""

    __tablename__ = "maintenance_runs"
    __table_args__ = (
        # "The newest success of this kind" is the only read, so it is the index.
        Index("ix_maintenance_runs_kind_finished_at", "kind", "finished_at"),
        CheckConstraint("kind IN ('backup', 'restore_drill', 'offsite')", name="kind_known"),
    )

    #: Redeclared only to add a server default: `scripts/backup.sh` inserts with
    #: `psql`, which cannot mint a UUIDv7. Rows written from Python still get one.
    id: Mapped[EntityId] = mapped_column(
        primary_key=True, default=new_id, server_default=text("gen_random_uuid()")
    )
    #: A plain string checked by the database rather than an enum column: the
    #: writer of half these rows is a shell script running `psql`, and the CHECK
    #: is what it is checked against.
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    #: When the job finished, on the database's clock — the scrape compares it
    #: with `time()`, and both writers can reach `now()` but only one has Python.
    finished_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    #: What the run was about — the dump file's name — for the person reading why.
    detail: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")


async def record_success(db: AsyncSession, kind: RunKind, *, detail: str = "") -> None:
    """Append the row a successful run leaves behind."""
    db.add(MaintenanceRun(kind=kind.value, detail=detail[:300]))
    await db.flush()


async def last_successes(db: AsyncSession) -> dict[RunKind, datetime]:
    """The newest success of each kind. A kind that never succeeded is absent."""
    rows = await db.execute(
        select(MaintenanceRun.kind, func.max(MaintenanceRun.finished_at)).group_by(
            MaintenanceRun.kind
        )
    )
    known = {kind.value: kind for kind in RunKind}
    return {known[kind]: at for kind, at in rows.all() if kind in known and at is not None}


__all__ = ["MaintenanceRun", "RunKind", "last_successes", "record_success"]
