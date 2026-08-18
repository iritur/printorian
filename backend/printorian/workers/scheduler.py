"""The scheduler tick.

Plans ready work onto machines and sends it, on an interval and — more
importantly — the moment something happens that could change the answer.

**Why events, not just a timer.** ARCHITECTURE §6 asks for a re-plan on
`job.ready`, `printer.became_free`, `material.mounted` and priority changes. On a
30-second tick alone, a printer that finishes at 09:00:01 stands idle until
09:00:30 with work sitting in the queue. Multiply by a farm and a day and that is
real capacity thrown away for nothing. The interval becomes the safety net for
anything the events miss, rather than the primary mechanism.

Composing fleet and production is this layer's job (ARCHITECTURE §layering):
the fleet owns what machines can do, production owns what jobs need, and neither
imports the other.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from decimal import Decimal

import structlog

from printorian.contexts.fleet import FleetService
from printorian.contexts.production import JobStatus, ProductionService
from printorian.contexts.production.prep import assigned_jobs, queued_minutes_by_printer
from printorian.contexts.scheduling import SchedulablePrinter, SchedulingPolicy
from printorian.core.errors import PrintorianError
from printorian.core.events import EventBus
from printorian.drivers import PrinterDriver

logger = structlog.get_logger(__name__)

#: Events that can change what the planner would decide. Anything here wakes the
#: loop immediately instead of waiting for the next tick.
REPLAN_TRIGGERS: tuple[str, ...] = (
    "job.ready",
    "printer.became_free",
    "fleet.printer_state_changed",
)


@dataclass(frozen=True, slots=True)
class TickOutcome:
    """What one pass achieved, for logging and the health endpoint."""

    considered: int = 0
    assigned: int = 0
    wait_listed: int = 0
    dispatched: int = 0
    dispatch_failed: int = 0


class SchedulerTick:
    """One planning-and-dispatch pass over the farm."""

    def __init__(
        self,
        production: ProductionService,
        fleet: FleetService,
        drivers: dict[str, PrinterDriver],
        *,
        policy: SchedulingPolicy | None = None,
    ) -> None:
        self._production = production
        self._fleet = fleet
        self._drivers = drivers
        self._policy = policy

    async def tick(self) -> TickOutcome:
        """Plan, then send what was planned."""
        printers = await self.schedulable_printers()
        plan = await self._production.plan_pass(printers, policy=self._policy)
        dispatched, failed = await self._dispatch_ready()
        return TickOutcome(
            considered=plan.considered,
            assigned=plan.assigned,
            wait_listed=plan.wait_listed,
            dispatched=dispatched,
            dispatch_failed=failed,
        )

    async def schedulable_printers(self) -> list[SchedulablePrinter]:
        """The fleet as the planner needs to see it.

        Capabilities come from the fleet; when each machine is next free and what
        it costs per hour come from the same context's view; queue depth comes
        from production. Assembling them here is what keeps either context from
        having to know about the other.
        """
        capabilities = await self._fleet.capabilities()
        table = await self._fleet.table()
        queued = await queued_minutes_by_printer(self._production.session)

        by_id = {str(row.id): row for row in table.rows}
        printers: list[SchedulablePrinter] = []
        for capability in capabilities:
            row = by_id.get(capability.printer_id)
            printers.append(
                SchedulablePrinter(
                    capability=capability,
                    # `eta` is only set while a machine is printing, which is
                    # exactly when a free-at time is meaningful. A machine with no
                    # ETA contributes no prediction rather than counting as free.
                    free_at=row.eta if row else None,
                    amortization_per_hour=(
                        Decimal(row.amortization_per_hour) if row else Decimal(0)
                    ),
                    queued_minutes=queued.get(capability.printer_id, Decimal(0)),
                )
            )
        return printers

    async def _dispatch_ready(self) -> tuple[int, int]:
        """Send every assigned job to its machine.

        Sequential rather than concurrent: two dispatches racing on the same
        session would interleave writes to the same job rows. Throughput is not
        the constraint here — a farm assigns a handful of jobs per tick.
        """
        dispatched = 0
        failed = 0
        for job in await assigned_jobs(self._production.session):
            driver = self._drivers.get(str(job.printer_id)) if job.printer_id else None
            try:
                result = await self._production.dispatch(job.id, driver)
            except PrintorianError as exc:
                # One job that cannot be sent must not end the pass for the rest.
                logger.warning("dispatch_failed", job_id=str(job.id), code=exc.code)
                failed += 1
                continue

            if result.status is JobStatus.PRINTING:
                dispatched += 1
            else:
                # Back in the queue with a reason recorded — see `dispatch`.
                failed += 1
        return dispatched, failed


def attach_replanning(bus: EventBus, wake: asyncio.Event) -> None:
    """Wake the scheduler whenever something could change the plan.

    Only sets a flag; the planning itself happens on the loop's own task with its
    own database session. Doing the work inside a handler would run it on whatever
    session published the event, in the middle of that caller's transaction.
    """

    async def _on_event(_event: object) -> None:
        wake.set()

    for pattern in REPLAN_TRIGGERS:
        bus.subscribe_pattern(pattern, _on_event)


async def run_forever(
    build_tick: object,
    *,
    interval_seconds: int,
    wake: asyncio.Event | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Tick on an interval, or as soon as something wakes us.

    `build_tick` is a callable returning a fresh `SchedulerTick` per pass, because
    each pass needs its own database session — reusing one across hours of ticks
    holds a transaction open for the farm's lifetime.
    """
    stop = stop or asyncio.Event()
    wake = wake or asyncio.Event()

    while not stop.is_set():
        try:
            tick = await build_tick()  # type: ignore[operator]
            outcome = await tick.tick()
            if outcome.considered or outcome.dispatched:
                logger.info(
                    "scheduler_tick",
                    considered=outcome.considered,
                    assigned=outcome.assigned,
                    wait_listed=outcome.wait_listed,
                    dispatched=outcome.dispatched,
                    dispatch_failed=outcome.dispatch_failed,
                )
        except Exception:
            # A bad pass is logged and the loop continues: one failure must not
            # silently stop the farm from ever scheduling again.
            logger.exception("scheduler_tick_failed")

        # Cleared *before* waiting, so an event that arrives during a pass is not
        # swallowed — it wakes the next one immediately instead of being lost.
        wake.clear()
        waiters = [asyncio.create_task(stop.wait()), asyncio.create_task(wake.wait())]
        try:
            _done, pending = await asyncio.wait(
                waiters, timeout=interval_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        except asyncio.CancelledError:
            for task in waiters:
                task.cancel()
            raise


__all__ = [
    "REPLAN_TRIGGERS",
    "SchedulerTick",
    "TickOutcome",
    "attach_replanning",
    "run_forever",
]
