"""FastAPI dependencies: database sessions, the current actor, permission gates.

Authorization is enforced *here*, in the delivery layer, for every route — never
by the client hiding a button (ARCHITECTURE §10).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.account import AccountService
from printorian.contexts.catalog import ModelLibrary, PlateLibrary
from printorian.contexts.fleet import FleetService
from printorian.contexts.identity import Actor, IdentityService, Permission
from printorian.contexts.journal import JournalService
from printorian.contexts.ordering import OrderingService
from printorian.contexts.packaging import PackagingService, PackingCatalogue
from printorian.contexts.payments import PaymentsService
from printorian.contexts.postproduction import PostProductionService
from printorian.contexts.production import ProductionService
from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.cpu import CpuGate
from printorian.core.errors import PermissionDeniedError, UnauthenticatedError
from printorian.core.events import EventBus
from printorian.core.ratelimit import Lockout, RateLimiter
from printorian.core.secrets import SecretBox
from printorian.core.storage import ObjectStore

#: Name of the cookie used by the storefront. Desktop and kiosk clients send a
#: bearer token instead; both resolve through the same session table.
SESSION_COOKIE = "printorian_session"


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.event_bus
    return bus


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session, committed on success."""
    async for session in request.app.state.database.session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
AppClock = Annotated[Clock, Depends(get_clock)]
AppEventBus = Annotated[EventBus, Depends(get_event_bus)]


def get_cpu(request: Request) -> CpuGate:
    """The bounded thread pool blocking work runs in (`core.cpu`)."""
    gate: CpuGate = request.app.state.cpu
    return gate


Cpu = Annotated[CpuGate, Depends(get_cpu)]


def get_lockout(request: Request) -> Lockout:
    lockout: Lockout = request.app.state.lockout
    return lockout


SignInLockout = Annotated[Lockout, Depends(get_lockout)]

#: Every rate ceiling is expressed per minute, so the window is stated once.
RATE_WINDOW = timedelta(minutes=1)


def rate_limited(bucket: str, allowance: Callable[[Settings], int]) -> Callable[..., None]:
    """Build a dependency that caps how often one address may hit these routes.

    ``bucket`` groups the routes sharing an allowance, so a caller cannot spend a
    quote budget on previews and then start again. Keyed on `client_ip`, which is
    client-controlled behind a proxy and is not evidence of identity — deliberately
    so: this bounds *cost*, and the endpoint it most needs to bound takes an
    optional actor, meaning there is frequently no identity to key on at all. An
    attacker who can vary their source address can buy more allowance; one who
    cannot is capped, and the CPU gate behind it (`core.cpu`) bounds what even an
    uncapped flood can occupy.

    Usage::

        dependencies=[Depends(rate_limited("quote", lambda s: s.quote_rate_per_minute))]
    """

    def guard(request: Request, settings: AppSettings) -> None:
        limiter: RateLimiter = request.app.state.limiter
        limiter.check(
            f"{bucket}:{throttle_key(request)}",
            limit=allowance(settings),
            window=RATE_WINDOW,
        )

    return guard


def throttle_key(request: Request) -> str:
    """The address a ceiling is counted against — the *last* forwarded hop.

    Deliberately **not** `client_ip`, and the difference is the whole point of
    having two functions.

    `X-Forwarded-For` is a list that each proxy appends to, so the *first* entry is
    whatever the caller sent and the *last* is the peer the nearest proxy actually
    saw. `client_ip` shows the first, because a security screen is telling a person
    "this is where the request said it came from" and a wrong answer there costs
    nothing. A rate limit keyed on the first entry costs everything: one forged
    header per request and the ceiling is a free-for-all, which is worse than no
    ceiling because it looks like one.

    So this reads the last hop, and falls back to the socket peer when nothing is
    forwarded at all — the dev server, and the console on the LAN.

    **This is exactly as trustworthy as the proxy in front of it.** It assumes one
    trusted hop that appends rather than replaces, which is what
    `deploy/console.Caddyfile` does and what the storefront's edge must also do
    (INFRASTRUCTURE Stage 3). Two proxies would need a hop count rather than "the
    last one"; when that day comes, this is the single line to change.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def get_object_store(request: Request) -> ObjectStore:
    store: ObjectStore = request.app.state.object_store
    return store


Storage = Annotated[ObjectStore, Depends(get_object_store)]


def get_model_library(db: DbSession, store: Storage, clock: AppClock, cpu: Cpu) -> ModelLibrary:
    return ModelLibrary(db, store, clock, cpu)


Models = Annotated[ModelLibrary, Depends(get_model_library)]


def get_account_service(db: DbSession) -> AccountService:
    return AccountService(db)


Account = Annotated[AccountService, Depends(get_account_service)]


def get_identity_service(
    db: DbSession, settings: AppSettings, clock: AppClock, bus: AppEventBus
) -> IdentityService:
    return IdentityService(db, settings, clock, bus)


Identity = Annotated[IdentityService, Depends(get_identity_service)]


def get_journal_service(db: DbSession, clock: AppClock) -> JournalService:
    return JournalService(db, clock)


Journal = Annotated[JournalService, Depends(get_journal_service)]


def get_ordering_service(db: DbSession, clock: AppClock, bus: AppEventBus) -> OrderingService:
    return OrderingService(db, clock, bus)


Ordering = Annotated[OrderingService, Depends(get_ordering_service)]


def get_payments_service(
    db: DbSession, clock: AppClock, bus: AppEventBus, ordering: Ordering
) -> PaymentsService:
    return PaymentsService(db, clock, bus, ordering)


Payments = Annotated[PaymentsService, Depends(get_payments_service)]


def get_fleet_service(
    db: DbSession, clock: AppClock, bus: AppEventBus, settings: AppSettings
) -> FleetService:
    return FleetService(db, clock, bus, SecretBox(settings.secret_key.get_secret_value()))


Fleet = Annotated[FleetService, Depends(get_fleet_service)]


def get_production_service(
    db: DbSession, clock: AppClock, bus: AppEventBus, store: Storage
) -> ProductionService:
    return ProductionService(db, clock, bus, store)


Production = Annotated[ProductionService, Depends(get_production_service)]


def get_postproduction_service(
    db: DbSession, clock: AppClock, bus: AppEventBus
) -> PostProductionService:
    return PostProductionService(db, clock, bus)


PostProduction = Annotated[PostProductionService, Depends(get_postproduction_service)]


def get_packaging_service(db: DbSession, clock: AppClock, bus: AppEventBus) -> PackagingService:
    return PackagingService(db, clock, bus)


Packaging = Annotated[PackagingService, Depends(get_packaging_service)]


def get_packing_catalogue(db: DbSession) -> PackingCatalogue:
    return PackingCatalogue(db)


PackingShelf = Annotated[PackingCatalogue, Depends(get_packing_catalogue)]


def get_plate_library(db: DbSession, clock: AppClock) -> PlateLibrary:
    return PlateLibrary(db, clock)


Plates = Annotated[PlateLibrary, Depends(get_plate_library)]


def session_token(request: Request) -> str:
    """The caller's session token, from the header or the cookie.

    Public because two routes need the token itself rather than the actor it
    resolves to: signing out, and «Завершить все, кроме текущего» — which has to
    know which session *is* the current one in order to spare it.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.removeprefix("Bearer ").strip()
    return request.cookies.get(SESSION_COOKIE, "")


def client_ip(request: Request) -> str:
    """Where the request came from, as best the API can tell.

    Behind the reverse proxy both apps are served through (ADR-0016), the socket
    peer is always the proxy, so the first hop of `X-Forwarded-For` is the only
    thing that varies per client. It is also client-controlled and therefore not
    evidence of anything — which is exactly why it is used here and nowhere near
    an authorization decision. The security screen shows it so a person can say
    "that was not me", and a wrong address is no worse than the proxy's own on
    every row.

    One place, so that the day this becomes a trust boundary there is one line to
    change rather than a search.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


async def get_current_actor(request: Request, identity: Identity) -> Actor:
    """Resolve the caller, or raise :class:`UnauthenticatedError`."""
    return await identity.resolve(session_token(request))


CurrentActor = Annotated[Actor, Depends(get_current_actor)]


async def get_optional_actor(request: Request, identity: Identity) -> Actor | None:
    """Resolve the caller if one is present; ``None`` for anonymous browsing."""
    try:
        return await identity.resolve(session_token(request))
    except UnauthenticatedError:
        return None


OptionalActor = Annotated[Actor | None, Depends(get_optional_actor)]


def requires(permission: Permission) -> Callable[[Actor], Actor]:
    """Build a dependency asserting the caller holds ``permission``.

    Usage::

        @router.get("/fleet", dependencies=[Depends(requires(Permission.MANAGE_FLEET))])
    """

    def guard(actor: CurrentActor) -> Actor:
        if not actor.can(permission):
            raise PermissionDeniedError(
                "error.permission_denied",
                permission=permission.value,
                role=actor.role.value,
            )
        return actor

    return guard
