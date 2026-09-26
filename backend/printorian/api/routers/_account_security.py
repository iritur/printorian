"""«Безопасность» — the password, and the sessions it opened.

Mounted on `/account` by `account.py`.

Two things here need the raw session token rather than the actor it resolves to:
listing sessions, which marks the caller's own, and ending all the others, which
has to spare it. `deps.session_token` is that one reader.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status

from printorian.api.deps import (
    SESSION_COOKIE,
    AppSettings,
    CurrentActor,
    Identity,
    SignInLockout,
    rate_limited,
    session_token,
    throttle_key,
)
from printorian.contexts.identity import ChangePassword, SessionView
from printorian.core.errors import UnauthenticatedError
from printorian.core.ids import EntityId

router = APIRouter()


@router.get("/sessions")
async def sessions(request: Request, actor: CurrentActor, identity: Identity) -> list[SessionView]:
    """Live sessions, newest first, with this one marked.

    Live means neither revoked nor expired. Listing an expired row would invite
    somebody to end a session that ended by itself, which teaches them the button
    does nothing.
    """
    return await identity.list_sessions(actor.user_id, current=session_token(request))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(session_id: EntityId, actor: CurrentActor, identity: Identity) -> None:
    """End one session — «Завершить» on a row.

    Ending your *own* from here is allowed and simply signs you out, which is
    what pressing it on the row marked «ТЕКУЩИЙ СЕАНС» would mean. The screen
    does not offer the button there, because a control whose effect is to close
    the screen is better absent than surprising.
    """
    await identity.revoke_session(actor.user_id, session_id)


@router.delete("/sessions")
async def end_other_sessions(
    request: Request, actor: CurrentActor, identity: Identity
) -> dict[str, int]:
    """«Завершить все, кроме текущего».

    The exception is the whole feature. Revoking everything is what deactivation
    does, and offering it here would mean the person tidying up after a lost
    laptop signs themselves out of the screen they are doing it from — leaving
    them to sign back in on the device they were worried about.
    """
    return {"ended": await identity.revoke_others(actor.user_id, keep=session_token(request))}


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    # The same per-address ceiling `/auth/*` carries. This route verifies a
    # password too, and a verifier with no ceiling is a verifier.
    dependencies=[Depends(rate_limited("auth", lambda s: s.auth_rate_per_minute))],
)
async def change_password(
    data: ChangePassword,
    request: Request,
    response: Response,
    actor: CurrentActor,
    identity: Identity,
    lockout: SignInLockout,
    settings: AppSettings,
) -> None:
    """Change the password, which ends every session including this one.

    That is the service's behaviour and it is the right one: a password is
    changed either as housekeeping or because it is believed to be known by
    somebody else, and the second case is worthless if the sessions it opened
    survive.

    The cookie is cleared here to match. Without it the browser keeps sending a
    token the server has already revoked, and the next request fails as
    *unauthenticated* rather than as *signed out* — the same outcome dressed as
    an error.

    **The lockout is the sign-in lockout, on a key of its own.** Somebody holding
    a stolen session could otherwise guess the *current* password here at whatever
    rate the hasher allows, with nothing counting — and on the hit they would own a
    password that survives every revocation the victim can perform. Keyed on the
    account and the address, as `sign_in` is, and the count is cleared only by a
    successful change (the security review, #27).
    """
    key = f"password|{actor.user_id}|{throttle_key(request)}"
    lockout.guard(key)
    try:
        await identity.change_password(
            actor.user_id, current=data.current, replacement=data.replacement
        )
    except UnauthenticatedError:
        lockout.record_failure(
            key,
            limit=settings.signin_max_attempts,
            penalty=timedelta(minutes=settings.signin_lockout_minutes),
        )
        raise
    lockout.clear(key)
    response.delete_cookie(SESSION_COOKIE, secure=request.url.scheme == "https")
