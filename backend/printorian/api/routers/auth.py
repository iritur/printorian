"""Registration, sign-in, sign-out, and "who am I"."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from printorian.api.deps import SESSION_COOKIE, CurrentActor, Identity, client_ip, session_token
from printorian.contexts.identity import Actor, CreateUser, Role, SessionGranted, SignIn, UserView

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.post("/sign-in")
async def sign_in(
    data: SignIn, request: Request, response: Response, identity: Identity
) -> SessionGranted:
    granted = await identity.sign_in(
        data,
        user_agent=request.headers.get("User-Agent"),
        client_ip=client_ip(request),
    )
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
