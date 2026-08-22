"""Refusing to serve production with test accounts in the database.

`docs/DEVELOPMENT.md` documents two accounts — `boss@printorian.example` and
`floor@printorian.example` — created by hand and described there with their
passwords. Nothing creates them anywhere else, and until now nothing refused them
either: `is_production` gated the mock driver, the mock payment provider and log
formatting, and nothing looked at who could sign in.

So a farm restored from a developer's dump kept a published password on an account
that can change every rate it charges. Not hypothetical — restoring a dump is the
normal way to seed a staging environment, and `RUNBOOK-BACKUP-RESTORE` describes
restoring one database into another as routine.

**Reserved domains make this checkable rather than guesswork.** RFC 2606 and RFC
6761 set aside `example.com`, `example.org`, `example.net` and the whole `.example`
TLD so that documentation can use addresses that will never resolve for anybody. An
account in one of them cannot be a real person at a real farm — which is what turns
"this looks like a test account" into a fact the code may act on.

The farm therefore refuses to *start* in production while one exists, rather than
warning. A warning about credentials is read after the incident; and the failure
this prevents — somebody signing in as the owner with a password published in a
public repository — is not one to leave running while a ticket ages.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity.models import User

#: Reserved by RFC 2606 (`example.com`/`.org`/`.net`) and RFC 6761 (the `.example`
#: TLD). None of them can receive mail, so none can belong to a farm.
RESERVED_SUFFIXES = ("@example.com", "@example.org", "@example.net", ".example")


async def reserved_domain_accounts(db: AsyncSession) -> list[str]:
    """Every account whose address is in a documentation-only domain.

    Returns addresses rather than a boolean: an operator who is being told the farm
    will not start needs to know *which* rows to remove, and a count would send
    them looking.
    """
    rows = await db.scalars(select(User.email))
    return sorted(
        email
        for email in rows
        if any(email.lower().endswith(suffix) for suffix in RESERVED_SUFFIXES)
    )


def refusal_message(accounts: list[str]) -> str:
    """What to print before refusing to serve."""
    listed = "\n".join(f"  {email}" for email in accounts)
    return (
        "Refusing to start: this database holds accounts in domains reserved for\n"
        "documentation (RFC 2606), which means it is a developer or test dump.\n"
        f"\n{listed}\n\n"
        "Their passwords are published in docs/DEVELOPMENT.md, and at least one of\n"
        "them is an owner.\n\n"
        "docs/RUNBOOK-FIRST-BOOT.md section 4 has the procedure. Read it before\n"
        "deleting anything: nothing referencing users restricts the delete, so it\n"
        "always succeeds, and rows it does not cascade away it keeps with the actor\n"
        "blanked out. On a farm being set up that is correct; on a database that\n"
        "turns out to hold real orders it erases who did what."
    )


__all__ = ["RESERVED_SUFFIXES", "refusal_message", "reserved_domain_accounts"]
