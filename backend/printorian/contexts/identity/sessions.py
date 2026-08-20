"""Listing and ending a person's own sessions.

Split out of `service.py` when the account screen's device panel pushed that file
past the length gate — which is the gate doing its job, because these three are a
different concern from the ones they were sitting among. Signing in mints a
session and resolving one authenticates a request, many times a second, on the
hot path; this is somebody looking at a list of their own devices and pressing a
button, rarely.

They take a session, an instant and an already-hashed token rather than the
service, so nothing here can reach a clock or a bus of its own — the same shape
as every other read module in this codebase. Hashing stays with the service: a
raw token has exactly one place it is allowed to be turned into a hash, and
spreading that across modules is how one of them ends up comparing the wrong
thing.

`IdentityService` still exposes all three. Callers were never asked to learn a
new object for a file-length problem that is none of their business.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity.models import Session
from printorian.contexts.identity.schemas import SessionView
from printorian.core.errors import NotFoundError
from printorian.core.ids import EntityId


async def live_sessions(
    db: AsyncSession, user_id: EntityId, *, now: datetime, current_hash: str = ""
) -> list[SessionView]:
    """Live sessions, newest first, with the caller's own one marked.

    Live means not revoked and not expired. An expired row is not a device
    somebody could still be signed in on, so listing it invites people to "end"
    sessions that ended by themselves — and the reaper deletes it anyway.
    """
    rows = await db.scalars(
        select(Session)
        .where(
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
        .order_by(Session.created_at.desc())
    )
    return [
        SessionView(
            id=row.id,
            user_agent=row.user_agent,
            client_ip=row.client_ip,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            is_current=bool(current_hash) and row.token_hash == current_hash,
        )
        for row in rows
    ]


async def revoke_one(
    db: AsyncSession, user_id: EntityId, session_id: EntityId, *, now: datetime
) -> None:
    """End one session. Scoped by owner, so an id from elsewhere finds nothing.

    `NotFoundError` rather than a permission error for a session belonging to
    somebody else: the two answers differ, and a caller who can tell them apart
    can probe for live session ids.
    """
    session = await db.scalar(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    if session is None:
        raise NotFoundError("error.identity.session_not_found", session_id=str(session_id))
    if session.revoked_at is None:
        session.revoked_at = now
        await db.flush()


async def revoke_others(
    db: AsyncSession, user_id: EntityId, *, keep_hash: str, now: datetime
) -> int:
    """End every session except the one making the request. Returns the count.

    `keep_hash` is the caller's own token, already hashed. Without it this ends
    all of them, which is what deactivation wants and what a person pressing
    «Завершить все, кроме текущего» very much does not — they would be signed out
    by their own click.
    """
    rows = await db.scalars(
        select(Session).where(
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
            Session.token_hash != keep_hash,
        )
    )
    ended = 0
    for session in rows:
        session.revoked_at = now
        ended += 1
    await db.flush()
    return ended


__all__ = ["live_sessions", "revoke_one", "revoke_others"]
