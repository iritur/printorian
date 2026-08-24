"""One pass, one session, one heartbeat.

Every ``run_forever`` loop asks for an object with `sweep()` (or `tick()`), and
these are those objects. What each one adds is the *session lifetime*: the loop
calls the factory once per pass and the pass ends before the next call, so opening
the session inside the pass keeps the commit point obvious and keeps a transaction
from spanning the life of the process — which would read a snapshot older than the
orders it is meant to notice, and never commit what it computed.

Each also records its beat, after the work rather than before it, so a loop that
starts a pass and hangs stops reporting healthy (`core.heartbeat`).

Split out of `runner` so that module is the loops and their lifetimes, and this one
is what a pass consists of.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import ModelLibrary
from printorian.contexts.fleet import FleetService
from printorian.contexts.fleet.models import Printer
from printorian.contexts.identity import IdentityService
from printorian.contexts.ordering import OrderingService
from printorian.contexts.packaging import PackagingService
from printorian.contexts.postproduction import PostProductionService
from printorian.contexts.production import ProductionService
from printorian.contexts.settings import SettingsService
from printorian.core.secrets import SecretBox
from printorian.workers import (
    maintenance,
    packaging,
    postproduction,
    scheduler,
    sla,
    telemetry,
)
from printorian.workers.drivers import DriverPool
from printorian.workers.runtime import WorkerRuntime


def fleet_service(runtime: WorkerRuntime, session: AsyncSession) -> FleetService:
    return FleetService(
        session,
        runtime.clock,
        runtime.bus,
        SecretBox(runtime.settings.secret_key.get_secret_value()),
    )


async def all_printers(session: AsyncSession) -> list[Printer]:
    """Every registered machine, active or not.

    Inactive ones are included deliberately: the pool needs to see them to close
    connections it is still holding to a printer somebody just retired.
    """
    return list(await session.scalars(select(Printer)))


class SlaPass:
    """A sweep that opens, uses and commits its own session."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> sla.SweepOutcome:
        async with self._runtime.session() as session:
            ordering = OrderingService(session, self._runtime.clock, self._runtime.bus)
            outcome = await sla.SlaSweep(ordering).sweep()
        await self._runtime.record_beat("sla", self._runtime.settings.sla_sweep_seconds)
        return outcome


class PostProductionPass:
    """A post-production pass that opens, uses and commits its own session."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> postproduction.SweepOutcome:
        async with self._runtime.session() as session:
            service = PostProductionService(session, self._runtime.clock, self._runtime.bus)
            outcome = await postproduction.PostProductionSweep(
                session, service, self._runtime.clock
            ).sweep()
        await self._runtime.record_beat(
            "postproduction", self._runtime.settings.postproduction_sweep_seconds
        )
        return outcome


class PackagingPass:
    """A packing pass that opens, uses and commits its own session."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> packaging.SweepOutcome:
        async with self._runtime.session() as session:
            service = PackagingService(session, self._runtime.clock, self._runtime.bus)
            outcome = await packaging.PackagingSweep(
                session, service, self._runtime.clock, self._runtime.settings.farm_timezone
            ).sweep()
        await self._runtime.record_beat("packaging", self._runtime.settings.packaging_sweep_seconds)
        return outcome


class MaintenancePass:
    """A maintenance pass that opens, uses and commits its own session."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime

    async def sweep(self) -> maintenance.MaintenanceOutcome:
        async with self._runtime.session() as session:
            identity = IdentityService(
                session, self._runtime.settings, self._runtime.clock, self._runtime.bus
            )
            models = ModelLibrary(
                session, self._runtime.object_store, self._runtime.clock, self._runtime.cpu
            )
            outcome = await maintenance.MaintenanceSweep(
                identity, models, session, self._runtime.clock, self._runtime.settings
            ).sweep()
        await self._runtime.record_beat(
            "maintenance", self._runtime.settings.maintenance_sweep_seconds
        )
        return outcome


class SchedulerPass:
    """One planning-and-dispatch pass, with its own session and live drivers."""

    def __init__(self, runtime: WorkerRuntime, pool: DriverPool) -> None:
        self._runtime = runtime
        self._pool = pool

    async def tick(self) -> scheduler.TickOutcome:
        async with self._runtime.session() as session:
            fleet = fleet_service(self._runtime, session)
            drivers = await self._pool.refresh(fleet, await all_printers(session))
            # The store is what turns a dispatch into real bytes on a printer;
            # without it `plate_to_send` refuses and every job returns to the queue.
            production = ProductionService(
                session,
                self._runtime.clock,
                self._runtime.bus,
                store=self._runtime.object_store,
            )
            # The scheduler weights live in the settings store and are resolved
            # per pass, so changing them affects the *next* planning decision
            # rather than the next restart.
            policy = await SettingsService(session, self._runtime.clock).resolve_scheduling()
            outcome = await scheduler.SchedulerTick(
                production, fleet, drivers, policy=policy
            ).tick()
        await self._runtime.record_beat("scheduler", self._runtime.settings.scheduler_tick_seconds)
        return outcome


class TelemetryPass:
    """One telemetry sweep, with its own session and the shared connections."""

    def __init__(self, runtime: WorkerRuntime, pool: DriverPool) -> None:
        self._runtime = runtime
        self._pool = pool

    async def sweep(self) -> telemetry.PollOutcome:
        async with self._runtime.session() as session:
            fleet = fleet_service(self._runtime, session)
            printers = await all_printers(session)
            drivers = await self._pool.refresh(fleet, printers)
            outcome = await telemetry.TelemetryPoller(fleet, drivers).sweep(printers)
        await self._runtime.record_beat("telemetry", self._runtime.settings.telemetry_poll_seconds)
        return outcome


__all__ = [
    "MaintenancePass",
    "PackagingPass",
    "PostProductionPass",
    "SchedulerPass",
    "SlaPass",
    "TelemetryPass",
    "all_printers",
    "fleet_service",
]
