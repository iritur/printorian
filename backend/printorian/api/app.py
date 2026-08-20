"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from printorian.api.errors import install_error_handlers
from printorian.api.routers import (
    account,
    auth,
    catalog,
    dashboard,
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
from printorian.api.ws import Hub
from printorian.api.ws import router as ws_router
from printorian.core.clock import SystemClock
from printorian.core.config import Settings, get_settings
from printorian.core.db import Database
from printorian.core.events import EventBus
from printorian.core.logging import configure_logging
from printorian.core.storage import build_object_store, prepare_root

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
        # Proved usable at startup rather than at first upload: a farm whose
        # storage directory is missing, read-only or on an unmounted disk should
        # fail to boot with a clear reason, not take an order and fail at prep.
        app.state.object_store = build_object_store(prepare_root(resolved.storage_root))

        # The hub turns published events into WebSocket traffic. Attached here so
        # every event a request emits reaches watching clients in the same process.
        app.state.hub = Hub()
        app.state.hub.attach(app.state.event_bus)
        try:
            yield
        finally:
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

    install_error_handlers(app)
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
    app.include_router(jobs.router)
    app.include_router(ws_router)
    return app
