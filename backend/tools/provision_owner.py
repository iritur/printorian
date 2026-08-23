"""Create the farm's first owner, on a deployment that has none.

Every account in this system is created by somebody who already has an account —
staff by an owner, customers by registering. That is the right rule and it leaves
one gap: a farm that has just been installed has nobody to do the creating. This
closes it, and it is the only way an account is ever made without an authenticated
actor behind it.

Run once, on the farm, after migrations::

    docker compose -f deploy/compose.prod.yml exec api \\
        python tools/provision_owner.py --email owner@thefarm.example

The password is **read from the terminal, never from an argument**. A password on
the command line is in the shell history, in `ps`, and in the container's process
list for as long as the command runs — and this is the one account that can change
every rate the farm charges.

The cost of that choice is that this needs a real TTY: `getpass` reads the terminal
rather than stdin, so it cannot be piped and it hangs forever without one. Do not
put this in a script, a systemd unit, or a `docker compose exec -T` — the hang is
indistinguishable from a slow database.

## It refuses rather than overwrites

If an owner already exists the script stops. Provisioning is a first-boot step, not
a password reset, and the two want opposite behaviour: a reset should be easy to
repeat and this should be impossible to repeat by accident. Somebody who has locked
themselves out needs the recovery path in the runbook, which is a deliberate act
with a record, not a script that quietly creates a second owner.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity import (
    RESERVED_SUFFIXES,
    CreateUser,
    IdentityService,
    Role,
)
from printorian.contexts.identity.models import User
from printorian.core.clock import SystemClock
from printorian.core.config import Settings, get_settings
from printorian.core.db import Database
from printorian.core.errors import PrintorianError
from printorian.core.events import EventBus


def _read_password() -> str:
    """Ask twice, and refuse anything the farm would refuse later.

    Checked here rather than only in `CreateUser` so the operator finds out while
    they are still standing at the terminal, rather than after a stack trace.
    """
    first = getpass.getpass("Password for the new owner: ")
    if len(first) < 10:
        raise SystemExit("Refused: the password must be at least 10 characters.")
    if first != getpass.getpass("Repeat it: "):
        raise SystemExit("Refused: the two entries did not match.")
    return first


async def provision_with(
    session: AsyncSession,
    settings: Settings,
    email: str,
    display_name: str,
    read_password: Callable[[], str] = _read_password,
) -> int:
    """The decision, given a session. Separated from the wiring so it can be run.

    A first-boot script is the worst place to discover a bug, because the farm it
    runs on is the one nobody has signed in to yet — there is no working state to
    compare against and no owner to ask. So the part that decides is a plain
    function over a session, and the test suite exercises it against a real empty
    database rather than trusting that it reads correctly.
    """
    owners = list(await session.scalars(select(User).where(User.role == Role.OWNER)))
    if owners:
        print("This farm already has an owner:", file=sys.stderr)
        for owner in owners:
            print(f"  {owner.email}", file=sys.stderr)
        print(
            "\nRefusing to create a second one. If you are locked out, see"
            "\ndocs/RUNBOOK-FIRST-BOOT.md - recovery is a deliberate act with"
            "\na record, not a re-run of this script.",
            file=sys.stderr,
        )
        return 1

    password = read_password()
    identity = IdentityService(session, settings, SystemClock(), EventBus())
    created = await identity.create_user(
        CreateUser(
            email=email,
            display_name=display_name,
            password=password,
            role=Role.OWNER,
        )
    )
    print(f"Created owner {created.email}. Sign in and change nothing else here.")
    return 0


async def provision(email: str, display_name: str) -> int:
    """Wiring only: open the farm's database, hand it to the decision, close it.

    The loop must run to exhaustion. `Database.session()` commits *after* the
    yield, so returning from inside the loop leaves the generator suspended and
    the commit never runs — the interpreter finalizes it later by throwing
    `GeneratorExit`, which is a `BaseException` and so slips past the
    `except Exception` that would have rolled back.

    That is not a stylistic point. The first version of this function returned
    from inside the loop: it created the owner, discarded the insert, printed
    "Created owner" and exited 0, on the one path in the system that runs exactly
    once on a farm nobody can yet sign in to.
    """
    settings = get_settings()
    database = Database(settings)
    code = 1
    try:
        async for session in database.session():
            code = await provision_with(session, settings, email, display_name)
    finally:
        await database.dispose()
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="The owner's address.")
    parser.add_argument(
        "--name",
        default="",
        help="Display name. Defaults to the part of the address before the @.",
    )
    args = parser.parse_args()

    email = args.email.strip()
    if get_settings().is_production and email.lower().endswith(RESERVED_SUFFIXES):
        raise SystemExit(
            f"Refused: {email} is in a domain reserved for documentation (RFC 2606).\n"
            "A production farm's owner needs an address that can receive mail - this\n"
            "is the account every password reset goes through."
        )

    try:
        return asyncio.run(provision(email, args.name.strip() or email.split("@")[0]))
    except PrintorianError as failure:
        raise SystemExit(f"Refused: {failure.code} {failure.details}") from failure


if __name__ == "__main__":
    raise SystemExit(main())
