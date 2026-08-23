"""Restore last night's dump into a scratch database and check that it works.

ARCHITECTURE §10 already says it: *untested backups are not backups*. This is the
thing that makes that sentence true rather than aspirational, and it is a scheduled
job rather than a quarterly intention because the failure it catches — a dump that
has been silently empty for weeks — is invisible until the day it matters.

Three assertions, in increasing order of how much they prove:

1. the dump restores at all;
2. ``alembic check`` finds no drift against it, so the restored schema is the
   schema this code expects — a dump from a database two migrations behind
   restores perfectly and is still useless;
3. the tables needed first in a real recovery hold what the live database holds.

The third is the one that catches the quiet failure. A backup script pointed at
the wrong database name produces a valid, restorable, empty dump every night, and
every check but this one passes.

It compares against the live database rather than demanding rows outright, because
a farm that has taken no payments yet legitimately has no `payment_notifications`
— and a drill that fails on a farm's first week is one that gets switched off
before it ever catches anything.

Run it::

    python -m scripts.restore_drill /backup/dumps/printorian-20260811T030000Z.dump

Exits non-zero on any failure, so cron mails the output.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from printorian.core.config import Settings

#: The scratch database. Dropped and recreated on every run, never reused.
DRILL_DATABASE = "printorian_restore_drill"

#: Tables compared between the live database and the restored copy.
#:
#: What a real recovery needs first, and each one empty means something different:
#: no users means the dump is of the wrong database, no orders means the work is
#: gone, no payment notifications means the record of what the gateway actually
#: said is gone — the hardest thing here to reconstruct by hand.
COMPARED = ("users", "orders", "payment_notifications")

#: The one table whose emptiness is a failure on its own terms.
#:
#: A farm always has an owner — `tools/provision_owner.py` is the first thing run
#: on it — so an empty `users` means the dump is not of this farm, regardless of
#: what the live database says.
NEVER_EMPTY = "users"


def _base_url() -> str:
    """Everything up to the database name, keeping SQLAlchemy's driver suffix.

    This used to strip `+asyncpg`, which handed SQLAlchemy a bare
    `postgresql://` URL and so selected psycopg2 — a driver this project declares
    only in its *dev* dependency group, for one migration test. The drill
    therefore ran on a developer's machine and died on the farm with
    `ModuleNotFoundError: No module named 'psycopg2'`, which is precisely the
    "works until it is the artifact" failure INFRASTRUCTURE §6 describes.
    """
    return Settings().database_url.rsplit("/", 1)[0]


def _libpq(url: str) -> str:
    """The same URL as the postgres command-line tools want it.

    `pg_restore` speaks libpq and knows nothing of SQLAlchemy's `+driver`
    suffix; it reads one as part of the scheme and refuses the URL.
    """
    return url.replace("+asyncpg", "")


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} failed:\n{result.stderr.strip()}")


async def recreate_drill_database() -> str:
    engine = create_async_engine(
        f"{_base_url()}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f"DROP DATABASE IF EXISTS {DRILL_DATABASE} WITH (FORCE)"))
            await connection.execute(text(f"CREATE DATABASE {DRILL_DATABASE}"))
    finally:
        await engine.dispose()
    return f"{_base_url()}/{DRILL_DATABASE}"


def restore(dump: Path, url: str) -> None:
    # `--no-owner` and `--no-privileges`: the drill runs as whoever the timer is,
    # which is not necessarily the role that owned the objects. A restore that
    # fails for that reason would be a false alarm, and a drill that cries wolf
    # gets ignored.
    _run(["pg_restore", "--dbname", _libpq(url), "--no-owner", "--no-privileges", str(dump)])


def assert_schema_current(url: str) -> None:
    """`alembic check` against the restored copy.

    A dump taken from a database two migrations behind restores perfectly and is
    still not something this code can run against. Without this the drill would
    pass right up until the recovery.
    """
    _run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            f"url={url}",
            "check",
        ]
    )


async def _counts(url: str) -> dict[str, int]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return {
                # Interpolated, not bound: an identifier cannot be a parameter.
                # Safe because `COMPARED` is a literal tuple in this file and
                # nothing outside it reaches this string.
                table: (
                    await connection.execute(text(f"SELECT count(*) FROM {table}"))
                ).scalar_one()
                for table in COMPARED
            }
    finally:
        await engine.dispose()


def compare(restored: dict[str, int], live: dict[str, int]) -> list[str]:
    """The drill's verdict, as arithmetic over two sets of counts.

    An earlier version asserted these tables were simply non-empty, which is the
    obvious check and the wrong one: a farm that has not yet taken a payment has no
    `payment_notifications`, so the drill failed on every farm on its first day —
    which is exactly when the first drills run. The script's own reasoning about
    `--no-owner` applies to itself: a drill that cries wolf gets ignored, and then
    it is not a drill.

    Comparing against the source keeps the failure it was really built to catch —
    a backup pointed at the wrong database, producing a valid, restorable, empty
    dump every night — while staying silent about tables that are legitimately
    empty on both sides.

    A plain function over two dicts rather than something that opens its own
    connections, because the failure directions are what need proving and a test
    that has to build two databases to prove them is a test nobody writes.

    Returns the lines to print. Raises `RuntimeError` if the drill has failed.
    """
    lines = []
    for table in COMPARED:
        if live[table] and not restored[table]:
            raise RuntimeError(
                f"restored {table!r} is empty but the live database has "
                f"{live[table]} rows - the dump is of the wrong database, or the "
                "backup has been failing silently"
            )
        note = "" if restored[table] else "  (empty in both - nothing to restore yet)"
        lines.append(f"  {table}: {restored[table]} rows, live {live[table]}{note}")

    if not restored[NEVER_EMPTY]:
        raise RuntimeError(
            f"restored {NEVER_EMPTY!r} is empty - a farm always has an owner, so "
            "this dump is not of a working farm"
        )
    return lines


async def assert_not_empty(url: str, source_url: str) -> None:
    """Read both databases and hand the counts to `compare`."""
    for line in compare(await _counts(url), await _counts(source_url)):
        print(line)


async def _drill(args: argparse.Namespace) -> int:
    try:
        print(f"restoring {args.dump.name} into {DRILL_DATABASE}")
        url = await recreate_drill_database()
        restore(args.dump, url)

        if not args.skip_schema_check:
            print("checking the restored schema matches the models")
            assert_schema_current(url)

        print("comparing the restored data against the live database")
        live = f"{_base_url()}/{Settings().database_url.rsplit('/', 1)[1]}"
        await assert_not_empty(url, live)
    except (RuntimeError, OSError) as exc:
        print(f"RESTORE DRILL FAILED: {exc}", file=sys.stderr)
        return 1

    print("restore drill passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="path to a pg_dump custom-format file")
    parser.add_argument(
        "--skip-schema-check",
        action="store_true",
        help="skip `alembic check` (use when drilling a deliberately old dump)",
    )
    args = parser.parse_args()

    if not args.dump.is_file():
        print(f"no such dump: {args.dump}", file=sys.stderr)
        return 2

    return asyncio.run(_drill(args))


if __name__ == "__main__":
    raise SystemExit(main())
