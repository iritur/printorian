"""«СООБЩИЛ ДРАЙВЕР» — opening and closing a failure from what the machine reported.

**A reconciling sweep, not an event subscriber**, and the argument is the one
`workers/postproduction.py` makes at length: `PrinterStateChanged` already fires on
every transition, and subscribing to it would be less code. It would also lose
work. An event delivered while this process is restarting is gone, and the failure
it was about is a hole in a history nothing can back-fill — `metric_rollups` can be
recomputed from `telemetry_samples`, and a failure that was never recorded has no
such second source. This pass instead asks "which machines are in `ERROR` with no
open failure" and "which open driver failures belong to a machine now observed
working", so a missed tick costs latency and never a record.

**`OFFLINE` is deliberately not a failure, and this is the judgement to read before
changing anything here.** `FleetService.mark_unreachable` writes `OFFLINE` for a
poll that did not answer — a dropped MQTT session, a switch rebooting, this very
process restarting. Treating that as a failure would mint one per machine on every
network blip and drive MTTR from repairs the farm never made. The cost is real and
is not hidden: a machine unreachable for half the month still contributes its
`offline_seconds` to `metric_rollups.observed_seconds`, so it reads as a reliable
machine that simply never broke. Unreachability is a *coverage* problem, and the
honest place to answer it is a coverage figure, not this table.

`OFFLINE` is excluded from the closing side too, for the mirrored reason: a machine
that went `ERROR` and then stopped answering has not been observed working again,
and `mark_unreachable` does not advance `last_seen_at`, so closing there would
compute a repair time out of an observation that saw nothing.

**Both timestamps come from the machine, never from this loop.** `detected_at` and
`restored_at` are `Printer.last_seen_at` — the moment of the observation that saw
the state. Using `clock.now()` would fold up to one sweep interval of invented
downtime into every repair (ADR-0007), and would make a measured figure depend on a
setting.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.models import Printer
from printorian.contexts.service import FailureOrigin, FailureView, ServiceDesk
from printorian.core.errors import PrintorianError
from printorian.drivers import PrinterState

logger = structlog.get_logger(__name__)

#: The states that open a failure. A set rather than a bare ``is ERROR`` so the two
#: exclusions are visible as an absence: `OFFLINE` is not here (see the module
#: docstring), and neither is `MAINTENANCE` — planned work is not an unplanned stop,
#: and counting it would make the best-maintained machine read as the worst one.
FAILING_STATES: frozenset[PrinterState] = frozenset({PrinterState.ERROR})


def is_working(state: PrinterState) -> bool:
    """Whether this state is the farm having *seen the machine working*.

    Everything except the failing states and `OFFLINE`. Spelled as a predicate
    rather than inlined because it is the half of the `OFFLINE` judgement that is
    easy to lose: excluding unreachable machines from opening a failure is obvious
    once stated, and excluding them from closing one is the part a later reader
    will try to simplify away.
    """
    return state not in FAILING_STATES and state is not PrinterState.OFFLINE


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """What one pass did, for logging and the health endpoint."""

    #: Failures opened from a machine the driver reported in `ERROR`.
    opened: int = 0
    #: Driver-opened failures closed by an observation that saw the machine working.
    closed: int = 0
    #: Machines whose record could not be written. The rest of the pass still ran.
    failed: int = 0


class ServiceSweep:
    """One reconciling pass over what the machines last reported about themselves."""

    def __init__(self, db: AsyncSession, desk: ServiceDesk) -> None:
        self._db = db
        self._desk = desk

    async def sweep(self) -> SweepOutcome:
        """Open what broke and close what came back, from the registry's own rows.

        Active machines only. A retired one keeps whatever state it was last
        observed in for ever, so sweeping it would open a failure about hardware
        that has left the farm and nothing would ever close it. Its *existing*
        failures stay on the table and keep counting in the window they happened in
        — `_service_reliability._roster` is the half that makes sure of that.
        """
        printers = list(await self._db.scalars(select(Printer).where(Printer.is_active)))
        if not printers:
            return SweepOutcome()

        # At most one open failure per machine is a database guarantee
        # (`uq_printer_failures_open`), so keying this by printer is safe rather
        # than lossy. Read once for the whole pass: the alternative is a query per
        # machine, every minute, over the whole farm.
        open_failures = {
            failure.printer_id: failure
            for failure in await self._desk.open_for([printer.id for printer in printers])
        }

        opened = closed = failed = 0
        for printer in printers:
            existing = open_failures.get(printer.id)
            try:
                if printer.state in FAILING_STATES:
                    opened += await self._open(printer, existing)
                elif existing is not None:
                    closed += await self._close(printer, existing)
            except PrintorianError as exc:
                # One machine whose record cannot be written must not leave the rest
                # of the farm's failures unrecorded for this pass. The reachable
                # case is a losing race on the partial unique index: another writer
                # opened the same failure between the read above and this insert,
                # which is exactly what that index is there for.
                failed += 1
                logger.warning(
                    "service_sweep_record_failed", printer_id=str(printer.id), code=exc.code
                )
        return SweepOutcome(opened=opened, closed=closed, failed=failed)

    async def _open(self, printer: Printer, existing: FailureView | None) -> int:
        """Open a failure for a machine the driver reported in `ERROR`."""
        if existing is not None:
            # Idempotence in Python, so the ordinary pass does not spend an insert
            # in order to be refused. The index behind it is what holds when this
            # check is wrong — a second worker process, or a pass that has not seen
            # the first one's commit.
            return 0
        if printer.last_seen_at is None:
            # Unreachable today: `ERROR` is written only by `FleetService.record`,
            # which sets `last_seen_at` in the same call. Kept because the answer if
            # it ever happens must not be `clock.now()` — that would date the
            # failure to the sweep and claim an observation nobody made.
            logger.warning("service_sweep_undated_error", printer_id=str(printer.id))
            return 0

        await self._desk.record(
            printer.id,
            FailureOrigin.DRIVER,
            # Verbatim, and no `cause`: `bambu.print_error.{code}` is what the
            # machine said, and the farm holds no table turning it into «слом
            # филамента». A person names the cause afterwards (ADR-0007).
            error_code=self._error_code(printer),
            detected_at=printer.last_seen_at,
        )
        return 1

    async def _close(self, printer: Printer, existing: FailureView) -> int:
        """Close a driver-opened failure on a machine that has been seen working.

        Driver-opened only. A failure somebody recorded by hand describes something
        the machine never reported — a jammed extruder it happily calls `IDLE` — so
        the machine's own state is no evidence it is over, and the person who opened
        it is the one who closes it.
        """
        if existing.origin is not FailureOrigin.DRIVER:
            return 0
        if not is_working(printer.state) or printer.last_seen_at is None:
            return 0
        await self._desk.restore(existing.id, printer.last_seen_at)
        return 1

    @staticmethod
    def _error_code(printer: Printer) -> str | None:
        """What the driver last said, or ``None`` for "it said nothing".

        `last_telemetry` is overwritten by every poll, so this is the code as of the
        observation that put the machine into `ERROR` — the same observation
        `detected_at` comes from. A missing key is ``None`` rather than a
        placeholder: "the driver reported no code" is a fact, and «нет данных»
        written into the column would be a sentence pretending to be one.
        """
        code = (printer.last_telemetry or {}).get("error_code")
        return code if isinstance(code, str) and code else None


async def run_forever(
    build_sweep: object,
    *,
    interval_seconds: int,
    stop: asyncio.Event | None = None,
) -> None:
    """Sweep on an interval until stopped.

    A timer alone, with no wake events, and the interval is cheaper here than it
    looks: both timestamps on the record come from the machine's own observation, so
    a longer interval delays when a failure *appears* on a screen and never changes
    the downtime the farm measured. That is the property that makes this loop safe
    to run rarely and safe to miss.

    `build_sweep` returns a fresh sweep per pass, each with its own session and its
    own commit, for the reason every other loop here does: one session held across
    hours reads a snapshot predating every state change it exists to notice.
    """
    stop = stop or asyncio.Event()

    while not stop.is_set():
        try:
            sweep = await build_sweep()  # type: ignore[operator]
            outcome = await sweep.sweep()
            if outcome.opened or outcome.closed or outcome.failed:
                logger.info(
                    "service_sweep",
                    opened=outcome.opened,
                    closed=outcome.closed,
                    failed=outcome.failed,
                )
        except Exception:
            # A bad pass is logged and the loop continues: one failure must not
            # leave every broken machine unrecorded for ever after.
            logger.exception("service_sweep_failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


__all__ = ["FAILING_STATES", "ServiceSweep", "SweepOutcome", "is_working", "run_forever"]
