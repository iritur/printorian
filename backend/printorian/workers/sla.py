"""The SLA clock.

Phase 2 shipped the promise and the decay policy; this is the thing that makes
them cost something. Without it `sla_credit` only ever moves when an order is
shipped, so a customer watching a late order sees zero owed right up until the
parcel leaves — and the one number the policy exists to produce is invisible for
exactly the period it is meant to cover.

**Why a timer alone, with no wake events.** The scheduler wakes on events because
its answer changes when the farm changes. This one's answer is a function of the
clock and nothing else: a credit grows because time passed, not because anything
happened. The two events that *do* matter — shipping, which stops the clock, and
the promise being made — are already handled where they occur, in
`OrderingService.advance`. Adding a wake here would fire on events that cannot
change the result.

The pass is recompute-only. `refresh_sla_credit` derives the figure from the
promise and the policy rather than adding to a running total, so a sweep that
runs twice, or a process that restarts mid-pass, cannot double-count.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

import structlog

from printorian.contexts.ordering import OrderingService
from printorian.core.errors import PrintorianError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """What one pass found, for logging and the health endpoint."""

    #: Orders past their promise and still owing work.
    overdue: int = 0
    #: Of those, the ones whose credit actually moved this pass.
    accrued: int = 0
    #: Orders that could not be recomputed. The rest of the pass still ran.
    failed: int = 0


class SlaSweep:
    """One pass over every order whose promise has expired."""

    def __init__(self, ordering: OrderingService) -> None:
        self._ordering = ordering

    async def sweep(self) -> SweepOutcome:
        """Recompute what lateness owes, for every order still accruing.

        Sequential rather than concurrent: these all share one session, and two
        recomputations interleaving would write the same order rows from two
        places. A farm has tens of overdue orders at worst, not thousands.
        """
        overdue = await self._ordering.overdue()
        accrued = 0
        failed = 0

        for view in overdue:
            before = view.sla_credit
            try:
                after = (await self._ordering.refresh_sla_credit(view.id)).sla_credit
            except PrintorianError as exc:
                # One order that cannot be recomputed — deleted mid-pass, say —
                # must not stop the others from accruing what they are owed.
                logger.warning("sla_refresh_failed", order_id=str(view.id), code=exc.code)
                failed += 1
                continue
            if after != before:
                accrued += 1

        return SweepOutcome(overdue=len(overdue), accrued=accrued, failed=failed)


async def run_forever(
    build_sweep: object,
    *,
    interval_seconds: int,
    stop: asyncio.Event | None = None,
) -> None:
    """Sweep on an interval until stopped.

    `build_sweep` is a callable returning a fresh `SlaSweep` per pass, because
    each pass needs its own database session and its own commit. Holding one
    across hours of sweeps would keep a transaction open for the farm's lifetime
    and read a snapshot that predates every order it is meant to notice.
    """
    stop = stop or asyncio.Event()

    while not stop.is_set():
        try:
            sweep = await build_sweep()  # type: ignore[operator]
            outcome = await sweep.sweep()
            if outcome.accrued or outcome.failed:
                logger.info(
                    "sla_sweep",
                    overdue=outcome.overdue,
                    accrued=outcome.accrued,
                    failed=outcome.failed,
                )
        except Exception:
            # A bad pass is logged and the loop continues: one failure must not
            # leave every late order accruing nothing for ever after.
            logger.exception("sla_sweep_failed")

        # Simpler than the scheduler's two-event race: there is only `stop` to
        # wait on, so a timeout is the whole of the interval logic. `wait_for`
        # cancels `stop.wait()` when the timeout wins, which loses nothing — the
        # event keeps its state and the next iteration waits on it again.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


__all__ = ["SlaSweep", "SweepOutcome", "run_forever"]
