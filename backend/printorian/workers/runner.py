"""The worker process.

Workers run beside the API, not inside it (`workers/__init__`). Two reasons that
matter in practice: a sweep holding a database session must not compete with
request handling for the same event loop, and a farm that runs several API
workers behind a proxy would otherwise run several copies of every clock — each
one recomputing the same credits and publishing the same events.

Started with::

    python -m printorian.workers

Each pass gets its own session, committed by `Database.session` on success and
rolled back on failure — the same contract request handlers get, for the same
reason: a worker that flushed without committing would compute the right number
and throw it away.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Registers every table on `Base.metadata`. A worker that imported only the
# context it sweeps would flush an order fine until the first foreign key to a
# table nobody imported — `orders.customer_id` → `users` — and then fail inside
# the unit of work, far from the missing import.
import printorian.models  # noqa: F401
from printorian.contexts.catalog import ModelLibrary
from printorian.contexts.fleet import FleetService
from printorian.contexts.fleet.models import Printer
from printorian.contexts.identity import IdentityService
from printorian.contexts.ordering import OrderingService
from printorian.contexts.packaging import PackagingService
from printorian.contexts.postproduction import PostProductionService
from printorian.contexts.production import ProductionService
from printorian.core.clock import SystemClock
from printorian.core.config import Settings, get_settings
from printorian.core.db import Database
from printorian.core.events import EventBus
from printorian.core.logging import configure_logging
from printorian.core.secrets import SecretBox
from printorian.core.storage import build_object_store, prepare_root
from printorian.workers import (
    maintenance,
    packaging,
    postproduction,
    scheduler,
    sla,
    telemetry,
)
from printorian.workers.drivers import DriverPool

logger = structlog.get_logger(__name__)


class WorkerRuntime:
    """The shared machinery every worker needs: settings, database, bus, clock."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.clock = SystemClock()
        self.bus = EventBus()
        self.database = Database(self.settings)
        # Same store the API writes through. Proved usable here too: the worker
        # collects models, and a collector that cannot reach the disk should say
        # so at startup rather than silently collecting nothing.
        self.object_store = build_object_store(prepare_root(self.settings.storage_root))

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """One unit of work, committed on success."""
        async for session in self.database.session():
            yield session

    async def dispose(self) -> None:
        await self.database.dispose()


async def _sla_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Run the SLA clock, giving each pass its own committed session.

    The loop lives in `workers.sla`; what belongs here is the session lifetime.
    `run_forever` calls the factory once per pass and the pass ends before the
    next call, so opening the session in the factory and closing it after the
    sweep would need the two halves to coordinate. Instead the sweep is driven
    directly, one session per iteration, which keeps the commit point obvious.
    """

    async def build() -> _SessionScopedSweep:
        return _SessionScopedSweep(runtime)

    await sla.run_forever(
        build,
        interval_seconds=runtime.settings.sla_sweep_seconds,
        stop=stop,
    )


async def _postproduction_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Turn finished prints into floor work, and end the drying timers.

    Same session-per-pass arrangement as the SLA loop, for the same reason.
    """

    async def build() -> _SessionScopedPostProduction:
        return _SessionScopedPostProduction(runtime)

    await postproduction.run_forever(
        build,
        interval_seconds=runtime.settings.postproduction_sweep_seconds,
        stop=stop,
    )


class _SessionScopedPostProduction:
    """A post-production pass that opens, uses and commits its own session."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> postproduction.SweepOutcome:
        async with self._runtime.session() as session:
            service = PostProductionService(session, self._runtime.clock, self._runtime.bus)
            return await postproduction.PostProductionSweep(
                session, service, self._runtime.clock
            ).sweep()


async def _packaging_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Turn inspected orders into parcels for the packing bench.

    Its own loop rather than a step inside the post-production pass, though the
    two are adjacent: the parcel is raised by *all* of an order's finishing work
    being done, which is a fact about the order and not about the task that
    happened to finish last.
    """

    async def build() -> _SessionScopedPackaging:
        return _SessionScopedPackaging(runtime)

    await packaging.run_forever(
        build,
        interval_seconds=runtime.settings.packaging_sweep_seconds,
        stop=stop,
    )


class _SessionScopedPackaging:
    """A packing pass that opens, uses and commits its own session."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> packaging.SweepOutcome:
        async with self._runtime.session() as session:
            service = PackagingService(session, self._runtime.clock, self._runtime.bus)
            return await packaging.PackagingSweep(
                session, service, self._runtime.clock, self._runtime.settings.farm_timezone
            ).sweep()


async def _maintenance_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Run housekeeping — partitions, retention, expired sessions.

    Same session-per-pass arrangement as the SLA loop, for the same reason.
    """

    async def build() -> _SessionScopedMaintenance:
        return _SessionScopedMaintenance(runtime)

    await maintenance.run_forever(
        build,
        interval_seconds=runtime.settings.maintenance_sweep_seconds,
        stop=stop,
    )


class _SessionScopedMaintenance:
    """A maintenance pass that opens, uses and commits its own session."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> maintenance.MaintenanceOutcome:
        async with self._runtime.session() as session:
            identity = IdentityService(
                session, self._runtime.settings, self._runtime.clock, self._runtime.bus
            )
            models = ModelLibrary(session, self._runtime.object_store, self._runtime.clock)
            return await maintenance.MaintenanceSweep(
                identity, models, session, self._runtime.clock, self._runtime.settings
            ).sweep()


async def _scheduler_forever(runtime: WorkerRuntime, pool: DriverPool, stop: asyncio.Event) -> None:
    """Plan work onto machines and send it — the pass Phase 4 is built around.

    Wakes on an event as well as on the interval. A printer that finishes a second
    after a tick must not stand idle for the rest of the interval with work in the
    queue, which on a farm is real capacity thrown away for nothing.
    """
    wake = asyncio.Event()
    scheduler.attach_replanning(runtime.bus, wake)

    async def build() -> _SessionScopedTick:
        return _SessionScopedTick(runtime, pool)

    await scheduler.run_forever(
        build,
        interval_seconds=runtime.settings.scheduler_tick_seconds,
        wake=wake,
        stop=stop,
    )


class _SessionScopedTick:
    """One planning-and-dispatch pass, with its own session and live drivers."""

    def __init__(self, runtime: WorkerRuntime, pool: DriverPool) -> None:
        self._runtime = runtime
        self._pool = pool

    async def tick(self) -> scheduler.TickOutcome:
        async with self._runtime.session() as session:
            fleet = _fleet_service(self._runtime, session)
            drivers = await self._pool.refresh(fleet, await _all_printers(session))
            # The store is what turns a dispatch into real bytes on a printer;
            # without it `plate_to_send` refuses and every job returns to the queue.
            production = ProductionService(
                session,
                self._runtime.clock,
                self._runtime.bus,
                store=self._runtime.object_store,
            )
            return await scheduler.SchedulerTick(production, fleet, drivers).tick()


async def _telemetry_forever(runtime: WorkerRuntime, pool: DriverPool, stop: asyncio.Event) -> None:
    """Ask every reachable machine what it is doing, and record the answer."""

    async def build() -> _SessionScopedPoll:
        return _SessionScopedPoll(runtime, pool)

    await telemetry.run_forever(
        build,
        interval_seconds=runtime.settings.telemetry_poll_seconds,
        stop=stop,
    )


class _SessionScopedPoll:
    """One telemetry sweep, with its own session and the shared connections."""

    def __init__(self, runtime: WorkerRuntime, pool: DriverPool) -> None:
        self._runtime = runtime
        self._pool = pool

    async def sweep(self) -> telemetry.PollOutcome:
        async with self._runtime.session() as session:
            fleet = _fleet_service(self._runtime, session)
            printers = await _all_printers(session)
            drivers = await self._pool.refresh(fleet, printers)
            return await telemetry.TelemetryPoller(fleet, drivers).sweep(printers)


def _fleet_service(runtime: WorkerRuntime, session: AsyncSession) -> FleetService:
    return FleetService(
        session,
        runtime.clock,
        runtime.bus,
        SecretBox(runtime.settings.secret_key.get_secret_value()),
    )


async def _all_printers(session: AsyncSession) -> list[Printer]:
    """Every registered machine, active or not.

    Inactive ones are included deliberately: the pool needs to see them to close
    connections it is still holding to a printer somebody just retired.
    """
    return list(await session.scalars(select(Printer)))


class _SessionScopedSweep:
    """A sweep that opens, uses and commits its own session.

    `sla.run_forever` asks for something with `sweep()`; this satisfies that while
    keeping the session's lifetime exactly one pass long. Without it the worker
    would either share one session across every sweep — a transaction open for
    the process's lifetime, reading a snapshot older than the orders it is meant
    to notice — or never commit at all.
    """

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> sla.SweepOutcome:
        async with self._runtime.session() as session:
            ordering = OrderingService(session, self._runtime.clock, self._runtime.bus)
            return await sla.SlaSweep(ordering).sweep()


async def main(settings: Settings | None = None) -> None:
    """Start every worker and run until the process is asked to stop."""
    runtime = WorkerRuntime(settings)
    configure_logging(runtime.settings)

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    logger.info(
        "workers_starting",
        scheduler_tick_seconds=runtime.settings.scheduler_tick_seconds,
        telemetry_poll_seconds=runtime.settings.telemetry_poll_seconds,
        sla_sweep_seconds=runtime.settings.sla_sweep_seconds,
        postproduction_sweep_seconds=runtime.settings.postproduction_sweep_seconds,
        maintenance_sweep_seconds=runtime.settings.maintenance_sweep_seconds,
    )
    # One pool, shared by the two loops that talk to printers. Two pools would
    # mean two MQTT sessions per machine, each unaware of the other's reconnects.
    pool = DriverPool(runtime.clock, runtime.settings)
    tasks = [
        asyncio.create_task(_scheduler_forever(runtime, pool, stop), name="scheduler"),
        asyncio.create_task(_telemetry_forever(runtime, pool, stop), name="telemetry"),
        asyncio.create_task(_sla_forever(runtime, stop), name="sla"),
        asyncio.create_task(_postproduction_forever(runtime, stop), name="postproduction"),
        asyncio.create_task(_packaging_forever(runtime, stop), name="packaging"),
        asyncio.create_task(_maintenance_forever(runtime, stop), name="maintenance"),
    ]
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # After the loops stop, so nothing is mid-upload when the socket closes.
        await pool.aclose()
        await runtime.dispose()
        logger.info("workers_stopped")


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Stop cleanly on SIGINT/SIGTERM where the platform supports it.

    Windows' event loop has no `add_signal_handler`, so there the process is
    stopped by KeyboardInterrupt instead — which `asyncio.run` already turns into
    cancellation of `main`, running the same shutdown path.
    """
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        received = getattr(signal, signal_name, None)
        if received is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(received, stop.set)


__all__ = ["WorkerRuntime", "main"]
