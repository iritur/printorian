"""FastAPI dependencies: database sessions, the current actor, permission gates.

Authorization is enforced *here*, in the delivery layer, for every route — never
by the client hiding a button (ARCHITECTURE §10).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import ModelLibrary, PlateLibrary
from printorian.contexts.fleet import FleetService
from printorian.contexts.identity import Actor, IdentityService, Permission
from printorian.contexts.journal import JournalService
from printorian.contexts.ordering import OrderingService
from printorian.contexts.payments import PaymentsService
from printorian.contexts.postproduction import PostProductionService
from printorian.contexts.production import ProductionService
from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.errors import PermissionDeniedError, UnauthenticatedError
from printorian.core.events import EventBus
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


def get_object_store(request: Request) -> ObjectStore:
    store: ObjectStore = request.app.state.object_store
    return store


Storage = Annotated[ObjectStore, Depends(get_object_store)]


def get_model_library(db: DbSession, store: Storage, clock: AppClock) -> ModelLibrary:
    return ModelLibrary(db, store, clock)


Models = Annotated[ModelLibrary, Depends(get_model_library)]


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


def get_plate_library(db: DbSession, clock: AppClock) -> PlateLibrary:
    return PlateLibrary(db, clock)


Plates = Annotated[PlateLibrary, Depends(get_plate_library)]


def _extract_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.removeprefix("Bearer ").strip()
    return request.cookies.get(SESSION_COOKIE, "")


async def get_current_actor(request: Request, identity: Identity) -> Actor:
    """Resolve the caller, or raise :class:`UnauthenticatedError`."""
    return await identity.resolve(_extract_token(request))


CurrentActor = Annotated[Actor, Depends(get_current_actor)]


async def get_optional_actor(request: Request, identity: Identity) -> Actor | None:
    """Resolve the caller if one is present; ``None`` for anonymous browsing."""
    try:
        return await identity.resolve(_extract_token(request))
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
