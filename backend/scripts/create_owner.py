"""Create the farm's first owner, and only ever the first.

A fresh database is a locked door. Public registration always produces a
customer — staff are created by somebody who already has an account (see
`auth.register`) — so a farm with no users has no way in, and every dev script
that makes its own database inherits that problem the moment it runs.

**It refuses on a populated database, and that is the whole design.** A bootstrap
that can run against a live farm is not a convenience, it is a privilege
escalation with a friendly name: anyone who reaches the server could mint
themselves an owner. Emptiness is the only condition under which granting the top
role needs no authority, because there is nobody to ask.

Usage::

    python scripts/create_owner.py
    PRINTORIAN_OWNER_EMAIL=me@farm python scripts/create_owner.py

Exit codes: 0 created, 0 already populated (nothing to do), 1 failed.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import printorian.models  # noqa: F401 - registers every table on the metadata
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.identity.models import User
from printorian.core.clock import SystemClock
from printorian.core.config import get_settings
from printorian.core.events import EventBus

#: Matches the credentials the README documents, so the two cannot drift.
DEFAULT_EMAIL = "boss@printorian.example"
DEFAULT_PASSWORD = "owner-pass-12345"


async def main() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            existing = await session.scalar(select(func.count()).select_from(User))
            if existing:
                print(f"{existing} account(s) already exist - nothing to do")
                return 0

            email = os.environ.get("PRINTORIAN_OWNER_EMAIL", DEFAULT_EMAIL)
            password = os.environ.get("PRINTORIAN_OWNER_PASSWORD", DEFAULT_PASSWORD)
            await IdentityService(session, settings, SystemClock(), EventBus()).create_user(
                CreateUser(email=email, display_name=email, password=password, role=Role.OWNER)
            )
            await session.commit()
            print(f"created owner {email}")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
