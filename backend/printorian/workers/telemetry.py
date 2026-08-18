"""The telemetry poller.

Asks every reachable machine what it is doing and records the answer. This is the
process that keeps the fleet view honest, and it is written around one rule from
ADR-0007: **a printer that cannot be reached is recorded as offline**, never left
showing its last happy state as though nothing had happened.

Manual printers are skipped rather than polled. There is nothing to ask: their state
is whatever a human last declared, and inventing a poll for them would either
overwrite that with nothing or fabricate a reading.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

import structlog

from printorian.contexts.fleet import ConnectionMode, FleetService
from printorian.contexts.fleet.models import Printer
from printorian.core.errors import PrintorianError
from printorian.drivers import DriverError, PrinterDriver

logger = structlog.get_logger(__name__)

#: Machines whose state comes from people, not from a wire.
_UNPOLLABLE = {ConnectionMode.MANUAL}


@dataclass(frozen=True, slots=True)
class PollOutcome:
    """What one sweep achieved, for logging and for the health endpoint."""

    polled: int = 0
    recorded: int = 0
    unreachable: int = 0
    skipped: int = 0


class TelemetryPoller:
    """Polls the fleet on a fixed interval."""

    def __init__(
        self,
        fleet: FleetService,
        drivers: dict[str, PrinterDriver],
        *,
        concurrency: int = 8,
    ) -> None:
        self._fleet = fleet
        self._drivers = drivers
        # A farm of fifty printers should not open fifty sockets at once; the cap
        # keeps one sweep from starving everything else on the box.
        self._limit = asyncio.Semaphore(concurrency)

    async def sweep(self, printers: list[Printer]) -> PollOutcome:
        """Poll every pollable printer once, concurrently."""
        pollable = [p for p in printers if p.connection_mode not in _UNPOLLABLE]
        skipped = len(printers) - len(pollable)

        results = await asyncio.gather(
            *(self._poll_one(printer) for printer in pollable), return_exceptions=True
        )

        recorded = sum(1 for result in results if result is True)
        unreachable = sum(1 for result in results if result is False)
        return PollOutcome(
            polled=len(pollable),
            recorded=recorded,
            unreachable=unreachable,
            skipped=skipped,
        )

    async def _poll_one(self, printer: Printer) -> bool:
        """Poll one machine. True when an observation was recorded."""
        async with self._limit:
            driver = self._drivers.get(str(printer.id))
            if driver is None:
                await self._fleet.mark_unreachable(printer.id, "no_driver")
                return False

            try:
                telemetry = await driver.read_telemetry()
            except DriverError as exc:
                # The whole point: a failure becomes a recorded Offline plus an
                # event, not a stale row that still looks alive.
                await self._fleet.mark_unreachable(printer.id, exc.code)
                return False
            except PrintorianError as exc:
                logger.warning("telemetry_poll_failed", printer=printer.name, code=exc.code)
                await self._fleet.mark_unreachable(printer.id, exc.code)
                return False

            await self._fleet.record(printer.id, telemetry)
            return True


async def run_forever(
    build_sweep: object,
    *,
    interval_seconds: int,
    stop: asyncio.Event | None = None,
) -> None:
    """Sweep on an interval until asked to stop.

    A failing sweep is logged and the loop continues: one unreachable printer, or
    one bad database moment, must not silently end fleet monitoring for everything.

    `build_sweep` returns a fresh sweep per pass, the same shape the SLA and
    maintenance loops take and for the same reason: a `FleetService` is bound to a
    database session, and one held across hours of polling would keep a
    transaction open for the process's lifetime and read a snapshot older than the
    printers it is meant to be watching.
    """
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            sweep = await build_sweep()  # type: ignore[operator]
            outcome = await sweep.sweep()
            logger.info(
                "telemetry_sweep",
                polled=outcome.polled,
                recorded=outcome.recorded,
                unreachable=outcome.unreachable,
                skipped=outcome.skipped,
            )
        except Exception:
            logger.exception("telemetry_sweep_failed")

        # Wait for the interval, but wake immediately if asked to stop, so a
        # shutdown does not have to sit through a full poll cycle.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
