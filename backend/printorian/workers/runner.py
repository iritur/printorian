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
and throw it away. The passes themselves live in `workers.passes`; the shared
machinery in `workers.runtime`; what is here is the loops and their lifetimes.

Everything these loops publish also goes onto the event relay
(`workers.runtime.WorkerRuntime.open`). That is not a detail: the API is a
different container, the bus is in-process, and without the relay every event a
sweep raises would die here — leaving the console's boards to load once and then
sit still while the farm worked.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import structlog

# Registers every table on `Base.metadata`. A worker that imported only the
# context it sweeps would flush an order fine until the first foreign key to a
# table nobody imported — `orders.customer_id` → `users` — and then fail inside
# the unit of work, far from the missing import.
import printorian.models  # noqa: F401
from printorian.core.config import Settings
from printorian.core.logging import configure_logging
from printorian.workers import (
    maintenance,
    packaging,
    postproduction,
    scheduler,
    sla,
    telemetry,
)
from printorian.workers.drivers import DriverPool
from printorian.workers.passes import (
    MaintenancePass,
    PackagingPass,
    PostProductionPass,
    SchedulerPass,
    SlaPass,
    TelemetryPass,
)
from printorian.workers.runtime import WorkerRuntime

logger = structlog.get_logger(__name__)


async def _sla_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Run the SLA clock, giving each pass its own committed session.

    The loop lives in `workers.sla`; what belongs here is the session lifetime.
    `run_forever` calls the factory once per pass and the pass ends before the
    next call, so opening the session in the factory and closing it after the
    sweep would need the two halves to coordinate. Instead the sweep is driven
    directly, one session per iteration, which keeps the commit point obvious.
    """

    async def build() -> SlaPass:
        return SlaPass(runtime)

    await sla.run_forever(
        build,
        interval_seconds=runtime.settings.sla_sweep_seconds,
        stop=stop,
    )


async def _postproduction_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Turn finished prints into floor work, and end the drying timers.

    Same session-per-pass arrangement as the SLA loop, for the same reason.
    """

    async def build() -> PostProductionPass:
        return PostProductionPass(runtime)

    await postproduction.run_forever(
        build,
        interval_seconds=runtime.settings.postproduction_sweep_seconds,
        stop=stop,
    )


async def _packaging_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Turn inspected orders into parcels for the packing bench.

    Its own loop rather than a step inside the post-production pass, though the
    two are adjacent: the parcel is raised by *all* of an order's finishing work
    being done, which is a fact about the order and not about the task that
    happened to finish last.
    """

    async def build() -> PackagingPass:
        return PackagingPass(runtime)

    await packaging.run_forever(
        build,
        interval_seconds=runtime.settings.packaging_sweep_seconds,
        stop=stop,
    )


async def _maintenance_forever(runtime: WorkerRuntime, stop: asyncio.Event) -> None:
    """Run housekeeping — partitions, retention, expired sessions.

    Same session-per-pass arrangement as the SLA loop, for the same reason.
    """

    async def build() -> MaintenancePass:
        return MaintenancePass(runtime)

    await maintenance.run_forever(
        build,
        interval_seconds=runtime.settings.maintenance_sweep_seconds,
        stop=stop,
    )


async def _scheduler_forever(runtime: WorkerRuntime, pool: DriverPool, stop: asyncio.Event) -> None:
    """Plan work onto machines and send it — the pass Phase 4 is built around.

    Wakes on an event as well as on the interval. A printer that finishes a second
    after a tick must not stand idle for the rest of the interval with work in the
    queue, which on a farm is real capacity thrown away for nothing.
    """
    wake = asyncio.Event()
    scheduler.attach_replanning(runtime.bus, wake)

    async def build() -> SchedulerPass:
        return SchedulerPass(runtime, pool)

    await scheduler.run_forever(
        build,
        interval_seconds=runtime.settings.scheduler_tick_seconds,
        wake=wake,
        stop=stop,
    )


async def _telemetry_forever(runtime: WorkerRuntime, pool: DriverPool, stop: asyncio.Event) -> None:
    """Ask every reachable machine what it is doing, and record the answer."""

    async def build() -> TelemetryPass:
        return TelemetryPass(runtime, pool)

    await telemetry.run_forever(
        build,
        interval_seconds=runtime.settings.telemetry_poll_seconds,
        stop=stop,
    )


async def main(settings: Settings | None = None) -> None:
    """Start every worker and run until the process is asked to stop."""
    runtime = WorkerRuntime(settings)
    configure_logging(runtime.settings)
    await runtime.open()

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
