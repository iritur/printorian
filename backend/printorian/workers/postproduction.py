"""Turning finished prints into work for a person, and ending the drying timers.

Two jobs, one pass, and both are **reconciling rather than reactive** — which is
the decision worth explaining.

The obvious design is an event subscriber: `job.succeeded` fires, a task is
raised. It is also the design that loses work. An event delivered while the
worker process is restarting is gone, and the part it was about sits on a bench
with nothing in the system saying so; the operator finds out when a customer
asks. This sweep instead asks "which succeeded jobs have no task yet" and makes
the missing ones, so a missed tick costs latency and never a lost batch. The same
reasoning runs through the scheduler: state is the truth, events are a hint about
when to look at it.

It composes three contexts, which is why it lives in `workers` rather than in any
of them: production owns the finished job, ordering owns the finishes the
customer paid for, post-production owns the task.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.postproduction import (
    CreateTask,
    OperationKind,
    PostProductionService,
    Task,
    TaskStatus,
)
from printorian.contexts.production import JobStatus, PrintJob
from printorian.core.clock import Clock
from printorian.core.errors import PrintorianError

logger = structlog.get_logger(__name__)

#: Which finish the customer chose maps to which operation the floor performs.
#: `raw` is deliberately absent: choosing no finish still means somebody takes the
#: part off the plate, and that is `SUPPORT_REMOVAL`, which every batch gets.
FINISH_OPERATIONS: dict[str, OperationKind] = {
    "sanded": OperationKind.SANDING,
    "primed": OperationKind.PRIMING,
    "painted": OperationKind.PAINTING,
    "polished": OperationKind.POLISHING,
}

#: Every batch begins here, whatever the customer ordered.
BASE_OPERATION = OperationKind.SUPPORT_REMOVAL

#: Most jobs one pass will convert. A farm with a larger backlog of unconverted
#: prints gets the rest on the next pass moments later.
RAISE_BATCH = 100


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """What one pass did, for logging and the health endpoint."""

    #: Tasks created from prints that had none.
    raised: int = 0
    #: Batches whose drying finished and which moved on to inspection.
    cured: int = 0
    #: Prints that could not be converted. The rest of the pass still ran.
    failed: int = 0


class PostProductionSweep:
    """One reconciling pass over finished prints and drying batches."""

    def __init__(self, db: AsyncSession, service: PostProductionService, clock: Clock) -> None:
        self._db = db
        self._service = service
        self._clock = clock

    async def sweep(self) -> SweepOutcome:
        raised, failed = await self._raise_missing()
        cured = await self._end_curing(self._clock.now())
        return SweepOutcome(raised=raised, cured=cured, failed=failed)

    async def _raise_missing(self) -> tuple[int, int]:
        """Create the tasks that finished prints imply and nobody has yet."""
        already = set(
            await self._db.scalars(select(Task.order_id).where(Task.status != TaskStatus.CANCELLED))
        )
        jobs = list(
            await self._db.scalars(
                select(PrintJob)
                .where(PrintJob.status == JobStatus.SUCCEEDED)
                # `id` as the tiebreak: jobs of one order finish together often
                # enough, and a bounded batch whose membership is arbitrary can
                # leave the same job unraised on pass after pass without ever
                # saying so (`core.pagination`).
                .order_by(PrintJob.finished_at.desc(), PrintJob.id.desc())
                .limit(RAISE_BATCH)
            )
        )

        raised = failed = 0
        for job in jobs:
            if job.order_id in already:
                continue
            try:
                raised += await self._raise_for(job)
            except PrintorianError as exc:
                # One order that cannot be converted — an operation nobody has
                # written an instruction for, say — must not stop the rest.
                failed += 1
                logger.warning(
                    "postproduction.raise_failed", order_id=str(job.order_id), code=exc.code
                )
            else:
                already.add(job.order_id)
        return raised, failed

    async def _raise_for(self, job: PrintJob) -> int:
        """One print's worth of tasks: the base operation, then the finishes."""
        order = await self._db.get(Order, job.order_id)
        if order is None:  # pragma: no cover — the FK on jobs prevents this
            return 0
        lines = list(
            await self._db.scalars(select(OrderLine).where(OrderLine.order_id == order.id))
        )

        made = 0
        for line in lines:
            for kind in _operations_for(line.finishes):
                await self._service.raise_task(
                    CreateTask(
                        order_id=order.id,
                        kind=kind,
                        model_name=line.model_name,
                        material_code=line.material_code,
                        colors=list(line.colors),
                        printer_id=job.printer_id,
                        quantity=line.quantity,
                        # The order's own promise, so the board's urgency is the
                        # customer's deadline rather than a target the shop set
                        # for itself.
                        due_at=order.promised_at,
                    )
                )
                made += 1
        return made

    async def _end_curing(self, now: datetime) -> int:
        """Move batches whose drying timer has run out on to inspection."""
        drying = list(
            await self._db.scalars(
                select(Task).where(
                    Task.status == TaskStatus.CURING,
                    Task.cure_until.is_not(None),
                    Task.cure_until <= now,
                )
            )
        )
        for task in drying:
            await self._service.cured(task.id)
        return len(drying)


def _operations_for(finishes: list[str]) -> list[OperationKind]:
    """Which operations a line's chosen finishes imply, in the order they happen.

    Order matters and is not the customer's: priming before painting whatever
    sequence the options were ticked in. The enum's own declaration order is that
    sequence, so sorting by it keeps one definition of "what happens first".
    """
    wanted = {BASE_OPERATION}
    wanted.update(FINISH_OPERATIONS[finish] for finish in finishes if finish in FINISH_OPERATIONS)
    order = list(OperationKind)
    return sorted(wanted, key=order.index)


async def run_forever(
    build_sweep: object,
    *,
    interval_seconds: int,
    stop: asyncio.Event | None = None,
) -> None:
    """Sweep on an interval until stopped.

    A timer alone, with no wake events, for the same reason the SLA sweep uses
    one: a drying timer expires because time passed. A print that finished a
    second after a tick waits one interval, which is nothing against a two-hour
    cure — and because the pass reconciles rather than reacts, waiting costs
    latency and never a lost batch.

    `build_sweep` returns a fresh sweep per pass, each with its own session and
    its own commit. Holding one across hours would keep a transaction open for
    the farm's lifetime and read a snapshot predating every print it should
    notice.
    """
    stop = stop or asyncio.Event()

    while not stop.is_set():
        try:
            sweep = await build_sweep()  # type: ignore[operator]
            outcome = await sweep.sweep()
            if outcome.raised or outcome.cured or outcome.failed:
                logger.info(
                    "postproduction_sweep",
                    raised=outcome.raised,
                    cured=outcome.cured,
                    failed=outcome.failed,
                )
        except Exception:
            # A bad pass is logged and the loop continues: one failure must not
            # leave every finished print invisible to the floor for ever after.
            logger.exception("postproduction_sweep_failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


__all__ = [
    "BASE_OPERATION",
    "FINISH_OPERATIONS",
    "RAISE_BATCH",
    "PostProductionSweep",
    "SweepOutcome",
    "run_forever",
]
