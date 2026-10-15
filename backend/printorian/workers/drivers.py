"""Live driver connections, one per printer, shared by the workers that need them.

The scheduler and the telemetry poller both take ``dict[str, PrinterDriver]``.
Building that dictionary per pass would be the obvious implementation and the
wrong one: a Bambu driver holds an MQTT/TLS session, so a fresh one every tick is
a reconnect every tick — on a fifty-machine farm at a thirty-second interval,
a hundred connection attempts a minute against printers that were already
connected. ARCHITECTURE names reconnect storms as a real failure mode; this is
where one would come from.

So the pool **keeps drivers alive between passes** and rebuilds one only when the
printer's connection details actually change. What counts as a change is the
fingerprint below: brand, mode, host, serial, and whether an access code is set.
A printer that was merely renamed does not lose its connection.

Failure to connect is not cached as an error. A machine that was unplugged at
09:00 must be able to come back at 09:05 without restarting the process, so a
failed build is simply absent from the dictionary and retried. The callers
already know what to do with a printer that has no driver: the poller records it
offline (ADR-0007), and the scheduler cannot dispatch to it.

**Retried on a backoff, not on every pass, and several at once** (issue #26). A
connect attempt against a machine that is switched on but not answering takes a
full minute — paho bounds the TLS handshake by the MQTT keepalive — and the
telemetry loop runs every five seconds. Retrying every printer on every pass, one
after another, made the passes themselves the reconnect storm ROADMAP warns
about: measured with `tools/measure_driver_pool.py`, one silent printer cost a
60-second pass and four cost 240. So a printer that failed is next tried after
`RETRY_FIRST_SECONDS`, doubling to `RETRY_CAP_SECONDS`, each delay scaled by a
random factor in [0.5, 1) so that a farm-wide outage — a router reboot — does not
bring every machine back in the same second. Changing the printer's connection
details retries at once: that is somebody fixing it. And the attempts that are
due run together, at most `connect_concurrency` at a time, so a pass waits for
the slowest attempt rather than for the sum of them.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from printorian.contexts.fleet import FleetService
from printorian.contexts.fleet.models import Printer
from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.driver_health import CONNECTED, UNAVAILABLE, DriverHealth
from printorian.core.errors import PrintorianError
from printorian.drivers import DriverError, PrinterDriver
from printorian.drivers import registry as driver_registry

logger = structlog.get_logger(__name__)

#: The first retry after a failed connect. Long enough that a five-second
#: telemetry loop is not a connect attempt every five seconds; short enough that
#: a machine whose plug was only knocked is back within a minute.
RETRY_FIRST_SECONDS = 30
#: The longest a failing printer waits between attempts. Five minutes keeps the
#: module's promise — unplugged at 09:00, back at 09:05 — once the delay has
#: reached its ceiling.
RETRY_CAP_SECONDS = 300
#: Connect attempts in flight at once. The telemetry poller reads eight machines
#: at a time (`workers.telemetry`), and the same number bounds the threads a
#: farm-wide outage can hold in paho's minute-long handshakes.
CONNECT_CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class _Failure:
    """Why a printer has no driver, and since when.

    The code alone was enough while this only fed the deduplicated log line. It is
    not enough to *report*: "unavailable" and "unavailable since 03:14" are
    different facts, and only the second says whether anybody should walk over and
    look at the machine.
    """

    code: str
    since: str


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    """What must change before a live connection is worth discarding."""

    brand: str
    mode: str
    host: str | None
    serial: str | None
    #: Whether a code is set, never the code. A pool keyed on plaintext would put
    #: the credential in a dataclass repr and from there into a log line.
    has_code: bool

    @classmethod
    def of(cls, printer: Printer) -> _Fingerprint:
        return cls(
            brand=printer.brand,
            mode=printer.connection_mode.value,
            host=printer.host,
            serial=printer.serial or None,
            has_code=bool(printer.access_code_encrypted),
        )


@dataclass(frozen=True, slots=True)
class _Retry:
    """When a printer that failed to connect is next worth trying."""

    failures: int
    not_before: datetime
    #: The details the failed attempt used. Different details are a fix, and are
    #: tried at once rather than after the backoff.
    fingerprint: _Fingerprint


class DriverPool:
    """Owns the process's live printer connections."""

    def __init__(
        self,
        clock: Clock,
        settings: Settings,
        *,
        connect_concurrency: int = CONNECT_CONCURRENCY,
        jitter: random.Random | None = None,
    ) -> None:
        self._clock = clock
        self._settings = settings
        self._limit = asyncio.Semaphore(connect_concurrency)
        # Not a security use: it spreads retries out in time, nothing more.
        self._jitter = jitter or random.Random()
        # The scheduler and the telemetry poller share this pool and each call
        # `refresh` at the top of its pass. While connecting blocked the event
        # loop the two could not interleave; now that it does not, two passes
        # could each open a session to the same machine, and a printer with two
        # MQTT sessions has each unaware of the other. One refresh at a time.
        self._lock = asyncio.Lock()
        self._retry: dict[str, _Retry] = {}
        self._drivers: dict[str, PrinterDriver] = {}
        self._fingerprints: dict[str, _Fingerprint] = {}
        # The last failure reported for a printer, so a machine that is simply
        # switched off is logged once rather than on every pass. Six offline
        # printers on a thirty-second tick is otherwise 720 identical lines an
        # hour, which is how a log stops being read.
        self._last_failure: dict[str, _Failure] = {}
        # When each live connection was made. Set in `_connect`, which runs only
        # when a connection is actually (re)made, so it is the true start of the
        # current state rather than the time of the last pass that noticed.
        self._connected_since: dict[str, str] = {}
        # Every printer the pool was asked to drive on its last pass, by name.
        # What `states` reports on — the *observed* roster, never the `printers`
        # table, because a printer this process has never tried to reach is not
        # one it can say anything about (root CLAUDE.md §1).
        self._roster: dict[str, str] = {}

    async def refresh(
        self, fleet: FleetService, printers: list[Printer]
    ) -> dict[str, PrinterDriver]:
        """Connections for the printers given, connecting and retiring as needed.

        Called at the top of each pass with the fleet as it stands now, so a
        printer registered a minute ago is driven a minute later without a
        restart, and one deleted or deactivated has its socket closed rather than
        left open against a machine nobody is watching.
        """
        async with self._lock:
            wanted = {str(printer.id): printer for printer in printers}
            self._roster = {printer_id: printer.name for printer_id, printer in wanted.items()}
            # Gone from the fleet: close the socket *and* forget the printer
            # entirely, so one re-registered later is treated as new.
            await self._retire(set(self._drivers) - set(wanted), forget=True)
            for gone in set(self._retry) - set(wanted):
                del self._retry[gone]

            now = self._clock.now()
            due: list[tuple[Printer, _Fingerprint]] = []
            for printer_id, printer in wanted.items():
                fingerprint = _Fingerprint.of(printer)
                live = printer_id in self._drivers
                if live and self._fingerprints.get(printer_id) == fingerprint:
                    continue
                retry = self._retry.get(printer_id)
                if retry and retry.fingerprint == fingerprint and now < retry.not_before:
                    continue
                due.append((printer, fingerprint))

            await asyncio.gather(*(self._attempt(fleet, p, f) for p, f in due))
            return dict(self._drivers)

    async def _attempt(
        self, fleet: FleetService, printer: Printer, fingerprint: _Fingerprint
    ) -> None:
        async with self._limit:
            # Details changed under a live connection: close it before opening the
            # replacement, or the old session stays attached to the machine.
            #
            # `forget=False` matters here. This runs before every *retry* of an
            # unreachable printer too, and clearing its failure state would make
            # the reason look new each attempt — which is exactly the repeated
            # logging this is meant to prevent.
            await self._retire({str(printer.id)})
            await self._connect(fleet, printer, fingerprint)

    def states(self) -> list[DriverHealth]:
        """What this pool would say about each printer it was asked to drive.

        Pure: no I/O and no clock. Every timestamp it reports was recorded at the
        moment the state actually changed, so calling this twice in a pass cannot
        move a `since`, and a slow publish cannot age one.

        Every printer in the roster appears, the ones with no driver included.
        Reporting only what is connected would make an unreachable machine
        indistinguishable from one that was never registered — and the unreachable
        one is what the endpoint exists for.
        """
        report: list[DriverHealth] = []
        for printer_id, name in self._roster.items():
            if printer_id in self._drivers:
                report.append(
                    DriverHealth(
                        printer_id=printer_id,
                        name=name,
                        state=CONNECTED,
                        since=self._connected_since.get(printer_id),
                    )
                )
                continue
            failure = self._last_failure.get(printer_id)
            report.append(
                DriverHealth(
                    printer_id=printer_id,
                    name=name,
                    state=UNAVAILABLE,
                    code=failure.code if failure else None,
                    since=failure.since if failure else None,
                )
            )
        return report

    async def aclose(self) -> None:
        """Disconnect everything. Called once, on the way out."""
        await self._retire(set(self._drivers), forget=True)

    # -------------------------------------------------------------- internals

    async def _connect(
        self, fleet: FleetService, printer: Printer, fingerprint: _Fingerprint
    ) -> None:
        printer_id = str(printer.id)
        try:
            driver = driver_registry.build(
                printer.brand, fleet.connection_for(printer), self._clock, self._settings
            )
            await driver.connect(fleet.connection_for(printer))
        except (DriverError, PrintorianError) as exc:
            # Expected on a farm: a machine is off, or its code is wrong. Retried
            # next pass and never cached as a failure — but reported only when the
            # reason changes, so a printer that stays off says so once.
            code = getattr(exc, "code", type(exc).__name__)
            previous = self._last_failure.get(printer_id)
            # Compared on the code alone. Folding the timestamp into the comparison
            # would make every pass look like a new reason — restoring the per-pass
            # logging this dedupe exists to prevent, and resetting `since` on every
            # tick, so an outage could never grow older than one interval.
            if previous is None or previous.code != code:
                logger.info(
                    "driver_unavailable", printer_id=printer_id, brand=printer.brand, code=code
                )
                self._last_failure[printer_id] = _Failure(
                    code=code, since=self._clock.now().isoformat()
                )
            self._connected_since.pop(printer_id, None)
            self._schedule_retry(printer_id, fingerprint)
            return

        self._retry.pop(printer_id, None)
        self._drivers[printer_id] = driver
        self._fingerprints[printer_id] = fingerprint
        self._connected_since[printer_id] = self._clock.now().isoformat()
        # Worth a line every time it happens: a connection coming back is news,
        # and a machine that reconnects repeatedly is a fault worth seeing.
        failure = self._last_failure.pop(printer_id, None)
        logger.info(
            "driver_connected",
            printer_id=printer_id,
            brand=printer.brand,
            recovered=failure.code if failure else None,
        )

    def _schedule_retry(self, printer_id: str, fingerprint: _Fingerprint) -> None:
        previous = self._retry.get(printer_id)
        same = previous is not None and previous.fingerprint == fingerprint
        failures = previous.failures + 1 if previous and same else 1
        ceiling = min(RETRY_CAP_SECONDS, RETRY_FIRST_SECONDS * 2 ** (failures - 1))
        delay = ceiling * (0.5 + 0.5 * self._jitter.random())
        self._retry[printer_id] = _Retry(
            failures=failures,
            not_before=self._clock.now() + timedelta(seconds=delay),
            fingerprint=fingerprint,
        )

    async def _retire(self, printer_ids: set[str], *, forget: bool = False) -> None:
        for printer_id in printer_ids:
            driver = self._drivers.pop(printer_id, None)
            self._fingerprints.pop(printer_id, None)
            self._connected_since.pop(printer_id, None)
            if forget:
                # Left the fleet. Dropping the failure state means a machine
                # re-registered later is reported again rather than staying silent.
                self._last_failure.pop(printer_id, None)
            if driver is None:
                continue
            try:
                await driver.disconnect()
            except Exception:
                # Shutting down a connection that is already broken is not worth
                # failing a pass over — the socket is going away either way.
                logger.debug("driver_disconnect_failed", printer_id=printer_id)


__all__ = ["DriverPool"]
