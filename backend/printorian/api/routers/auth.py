"""Registration, sign-in, sign-out, and "who am I"."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status

from printorian.api.deps import (
    SESSION_COOKIE,
    AppSettings,
    CurrentActor,
    Identity,
    SignInLockout,
    client_ip,
    rate_limited,
    session_token,
    throttle_key,
)
from printorian.contexts.identity import Actor, CreateUser, Role, SessionGranted, SignIn, UserView
from printorian.core.errors import UnauthenticatedError

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    # Every route here, including `/me`: the whole prefix is reachable without a
    # session by definition, and Argon2 makes the two that verify a password
    # expensive for the server as well as for whoever is guessing.
    dependencies=[Depends(rate_limited("auth", lambda s: s.auth_rate_per_minute))],
)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(data: CreateUser, identity: Identity) -> UserView:
    """Public self-registration. Always a customer — staff are created by an owner."""
    return await identity.create_user(
        CreateUser(
            email=data.email,
            display_name=data.display_name,
            password=data.password,
            role=Role.CUSTOMER,
            locale=data.locale,
        )
    )


def _lockout_key(email: str, address: str) -> str:
    """What a failed sign-in counts against.

    The **pair**, not the account alone. Counting failures per account would let
    anyone lock a customer out of their own shop by guessing at their email a
    dozen times, which turns a defence into a denial of service. Counting per
    address alone would let one shared office address lock out its colleagues.
    The pair is what an attacker actually has to hold constant to make progress.
    """
    return f"{email.strip().lower()}|{address}"


@router.post("/sign-in")
async def sign_in(
    data: SignIn,
    request: Request,
    response: Response,
    identity: Identity,
    lockout: SignInLockout,
    settings: AppSettings,
) -> SessionGranted:
    """Exchange credentials for a session, with a ceiling on guessing.

    `SignInFailed` has described itself as "the raw material for lockout and audit"
    since it was written, and only the audit half existed: nothing anywhere counted
    failures, so an unlimited number of attempts per second was correct behaviour.
    This is the other half, and it is enforced here rather than in the service
    because it is a property of the *endpoint's* exposure, not of the domain rule —
    a script seeding staff accounts calls the same service and should not be
    throttled for it.
    """
    # `throttle_key`, not `client_ip`: a lockout keyed on the address the *caller*
    # claims is one header away from being no lockout at all. `client_ip` is still
    # what gets recorded on the session below, because a security screen is
    # reporting what the request said rather than deciding anything on it.
    key = _lockout_key(data.email, throttle_key(request))
    lockout.guard(key)

    try:
        granted = await identity.sign_in(
            data,
            user_agent=request.headers.get("User-Agent"),
            client_ip=client_ip(request),
        )
    except UnauthenticatedError:
        lockout.record_failure(
            key,
            limit=settings.signin_max_attempts,
            penalty=timedelta(minutes=settings.signin_lockout_minutes),
        )
        raise

    # Only a *successful* sign-in clears the count. Clearing it on any completed
    # attempt would make the ceiling meaningless: one good password among the
    # guesses, or a rejection for any other reason, would reset the counter.
    lockout.clear(key)
    response.set_cookie(
        SESSION_COOKIE,
        granted.token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        expires=granted.expires_at,
    )
    return granted


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(request: Request, response: Response, identity: Identity) -> None:
    token = session_token(request)
    if token:
        await identity.sign_out(token)
    response.delete_cookie(SESSION_COOKIE)


@router.get("/me")
async def me(actor: CurrentActor) -> Actor:
    """The caller's identity and resolved permissions — what the UI renders from."""
    return actor
