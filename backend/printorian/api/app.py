"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from printorian.api.errors import install_error_handlers
from printorian.api.middleware import BodySizeLimitMiddleware, CorrelationIdMiddleware
from printorian.api.routers import (
    account,
    auth,
    catalog,
    dashboard,
    fleet,
    health,
    jobs,
    journal,
    materials,
    orders,
    packaging,
    payments,
    postproduction,
    pricing,
    printers,
    public,
    users,
)
from printorian.api.routers import (
    settings as settings_router,
)
from printorian.api.ws import Hub
from printorian.api.ws import router as ws_router
from printorian.contexts.identity import refusal_message, reserved_domain_accounts
from printorian.core.clock import SystemClock
from printorian.core.config import Settings, get_settings
from printorian.core.cpu import CpuGate
from printorian.core.db import Database
from printorian.core.events import EventBus
from printorian.core.heartbeat import Heartbeat
from printorian.core.logging import configure_logging
from printorian.core.ratelimit import Lockout, RateLimiter
from printorian.core.relay import EventRelay
from printorian.core.storage import build_object_store, prepare_root

logger = structlog.get_logger(__name__)

#: Bumped when the wire contract changes in a way clients must notice.
API_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests call this with overridden settings."""
    resolved = settings or get_settings()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.clock = SystemClock()
        app.state.event_bus = EventBus()
        app.state.database = Database(resolved)

        # Before anything else is allocated: a production farm holding accounts
        # from a developer dump is a farm whose owner password is published in
        # this repository, so it must not come up (`contexts.identity.reserved`).
        #
        # Placed immediately after the database and before every other resource
        # because raising here aborts the lifespan, and the shutdown half of a
        # lifespan that never finished starting does not run — anything started
        # above this line would be left behind.
        if resolved.is_production:
            await _refuse_reserved_accounts(app)

        # Proved usable at startup rather than at first upload: a farm whose
        # storage directory is missing, read-only or on an unmounted disk should
        # fail to boot with a clear reason, not take an order and fail at prep.
        app.state.object_store = build_object_store(prepare_root(resolved.storage_root))
        # Blocking work runs here, bounded, rather than on the loop that serves
        # every other request while it runs (`core.cpu`).
        app.state.cpu = CpuGate(resolved.cpu_workers)
        app.state.limiter = RateLimiter(app.state.clock)
        app.state.lockout = Lockout(app.state.clock)
        # Read-only here: the workers write the beats, this process reports them
        # at `/health/workers` (`core.heartbeat`).
        app.state.heartbeat = Heartbeat(resolved.redis_url)
        await app.state.heartbeat.start()

        # The hub turns published events into WebSocket traffic. Attached here so
        # every event a request emits reaches watching clients in the same process.
        app.state.hub = Hub()
        app.state.hub.attach(app.state.event_bus)

        # ...and the relay brings in the ones the *workers* raise, which is most of
        # what the farm does on its own. Without it those events die in the worker
        # process and the console's boards are live only for what a person clicked
        # (`core.relay`). `from_url` does not dial, so a farm whose Redis is down
        # still starts and still serves; the subscription retries behind the scenes.
        app.state.relay = None
        if resolved.events_relay_enabled:
            relay = EventRelay(resolved.redis_url, resolved.events_channel)
            await relay.start()
            relay.attach(app.state.event_bus)
            relay.listen(app.state.hub.broadcast_payload)
            app.state.relay = relay
        try:
            yield
        finally:
            if app.state.relay is not None:
                await app.state.relay.aclose()
            await app.state.heartbeat.aclose()
            await app.state.database.dispose()

    app = FastAPI(
        title="Printorian API",
        version=API_VERSION,
        summary="3D print farm management",
        lifespan=lifespan,
        # Stable operation ids keep the generated TypeScript client readable (ADR-0005).
        generate_unique_id_function=lambda route: f"{route.tags[0]}_{route.name}",
    )

    # Only when a deployment actually runs a cross-origin client (the desktop
    # console). Absent by default, so the storefront's same-origin setup is
    # unchanged and the API does not advertise itself to every page on the web.
    if resolved.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.allowed_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            allow_headers=["Authorization", "Content-Type"],
            # The console sends a bearer token, not a cookie. Leaving this off
            # means a listed origin still cannot make the browser attach someone's
            # session cookie to a cross-site request.
            allow_credentials=False,
        )

    # Inside the exception middleware by construction, which is what lets a body
    # that is too large come back as the API's error envelope rather than a bare
    # 500 from the server.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=resolved.max_upload_bytes)

    # Added last, which puts it outermost: `add_middleware` inserts at the front
    # of the stack. That is what makes the correlation id bound before anything
    # else runs, and the access line cover the whole request rather than the part
    # inside CORS.
    app.add_middleware(CorrelationIdMiddleware)

    install_error_handlers(app)
    _install_routers(app)
    return app


async def _refuse_reserved_accounts(app: FastAPI) -> None:
    """Stop the process if the database is a test one wearing production's clothes.

    A `RuntimeError` rather than a domain error: this is not a request failing, it
    is the farm declining to exist in this configuration, and it must not be
    catchable by anything that would then carry on serving.
    """
    accounts: list[str] = []
    async for session in app.state.database.session():
        accounts = await reserved_domain_accounts(session)
    if accounts:
        message = refusal_message(accounts)
        logger.error("refusing_to_start_with_reserved_accounts", accounts=accounts)
        raise RuntimeError(message)


def _install_routers(app: FastAPI) -> None:
    """Mount every router. Its own function so `create_app` stays readable."""
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(users.router)
    app.include_router(catalog.router)
    app.include_router(dashboard.router)
    app.include_router(journal.router)
    app.include_router(materials.router)
    app.include_router(pricing.router)
    app.include_router(public.router)
    app.include_router(orders.router)
    app.include_router(payments.router)
    app.include_router(postproduction.router)
    app.include_router(packaging.router)
    app.include_router(printers.router)
    app.include_router(settings_router.router)
    app.include_router(fleet.router)
    app.include_router(jobs.router)
    app.include_router(ws_router)
