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
3. the tables that would be needed first in a real recovery are not empty.

The third is the one that catches the quiet failure. A backup script pointed at
the wrong database name produces a valid, restorable, empty dump every night, and
every check but this one passes.

Run it::

    python -m scripts.restore_drill /backup/dumps/printorian-20260811T030000Z.dump

Exits non-zero on any failure, so cron mails the output.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from printorian.core.config import Settings

#: The scratch database. Dropped and recreated on every run, never reused.
DRILL_DATABASE = "printorian_restore_drill"

#: Tables that must not be empty in a restored copy.
#:
#: Chosen because they are what a real recovery needs first, and because each one
#: being empty means something different went wrong: no users means the dump is of
#: the wrong database, no orders means the farm has taken no work (worth an alert
#: either way), no payment notifications means the record of what the gateway
#: actually said is gone — which is the hardest thing here to reconstruct by hand.
MUST_HAVE_ROWS = ("users", "orders", "payment_notifications")


def _base_url() -> str:
    return Settings().database_url.replace("+asyncpg", "").rsplit("/", 1)[0]


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} failed:\n{result.stderr.strip()}")


def recreate_drill_database() -> str:
    engine = create_engine(
        f"{_base_url()}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        with engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {DRILL_DATABASE} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {DRILL_DATABASE}"))
    finally:
        engine.dispose()
    return f"{_base_url()}/{DRILL_DATABASE}"


def restore(dump: Path, url: str) -> None:
    # `--no-owner` and `--no-privileges`: the drill runs as whoever cron is, which
    # is not necessarily the role that owned the objects. A restore that fails for
    # that reason would be a false alarm, and a drill that cries wolf gets ignored.
    _run(["pg_restore", "--dbname", url, "--no-owner", "--no-privileges", str(dump)])


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
            f"url={url.replace('postgresql://', 'postgresql+asyncpg://')}",
            "check",
        ]
    )


def assert_not_empty(url: str) -> None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            for table in MUST_HAVE_ROWS:
                count = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                if count == 0:
                    raise RuntimeError(
                        f"restored {table!r} is empty — the dump is of the wrong database, "
                        "or the backup has been failing silently"
                    )
                print(f"  {table}: {count} rows")
    finally:
        engine.dispose()


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

    try:
        print(f"restoring {args.dump.name} into {DRILL_DATABASE}")
        url = recreate_drill_database()
        restore(args.dump, url)

        if not args.skip_schema_check:
            print("checking the restored schema matches the models")
            assert_schema_current(url)

        print("checking the restored data is not empty")
        assert_not_empty(url)
    except (RuntimeError, OSError) as exc:
        print(f"RESTORE DRILL FAILED: {exc}", file=sys.stderr)
        return 1

    print("restore drill passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
