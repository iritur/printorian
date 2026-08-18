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
failed build is simply absent from the dictionary and retried next pass. The
callers already know what to do with a printer that has no driver: the poller
records it offline (ADR-0007), and the scheduler cannot dispatch to it.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from printorian.contexts.fleet import FleetService
from printorian.contexts.fleet.models import Printer
from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.errors import PrintorianError
from printorian.drivers import DriverError, PrinterDriver
from printorian.drivers import registry as driver_registry

logger = structlog.get_logger(__name__)


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


class DriverPool:
    """Owns the process's live printer connections."""

    def __init__(self, clock: Clock, settings: Settings) -> None:
        self._clock = clock
        self._settings = settings
        self._drivers: dict[str, PrinterDriver] = {}
        self._fingerprints: dict[str, _Fingerprint] = {}
        # The last failure reported for a printer, so a machine that is simply
        # switched off is logged once rather than on every pass. Six offline
        # printers on a thirty-second tick is otherwise 720 identical lines an
        # hour, which is how a log stops being read.
        self._last_failure: dict[str, str] = {}

    async def refresh(
        self, fleet: FleetService, printers: list[Printer]
    ) -> dict[str, PrinterDriver]:
        """Connections for the printers given, connecting and retiring as needed.

        Called at the top of each pass with the fleet as it stands now, so a
        printer registered a minute ago is driven a minute later without a
        restart, and one deleted or deactivated has its socket closed rather than
        left open against a machine nobody is watching.
        """
        wanted = {str(printer.id): printer for printer in printers}
        # Gone from the fleet: close the socket *and* forget the printer entirely,
        # so one re-registered later is treated as new.
        await self._retire(set(self._drivers) - set(wanted), forget=True)

        for printer_id, printer in wanted.items():
            fingerprint = _Fingerprint.of(printer)
            if self._fingerprints.get(printer_id) == fingerprint and printer_id in self._drivers:
                continue
            # Details changed under a live connection: close it before opening the
            # replacement, or the old session stays attached to the machine.
            #
            # `forget=False` matters here. This runs before every *retry* of an
            # unreachable printer too, and clearing its failure state would make
            # the reason look new each pass — which is exactly the per-pass
            # logging this is meant to prevent.
            await self._retire({printer_id})
            await self._connect(fleet, printer, fingerprint)

        return dict(self._drivers)

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
            if self._last_failure.get(printer_id) != code:
                logger.info(
                    "driver_unavailable", printer_id=printer_id, brand=printer.brand, code=code
                )
                self._last_failure[printer_id] = code
            return

        self._drivers[printer_id] = driver
        self._fingerprints[printer_id] = fingerprint
        # Worth a line every time it happens: a connection coming back is news,
        # and a machine that reconnects repeatedly is a fault worth seeing.
        recovered = self._last_failure.pop(printer_id, None)
        logger.info(
            "driver_connected", printer_id=printer_id, brand=printer.brand, recovered=recovered
        )

    async def _retire(self, printer_ids: set[str], *, forget: bool = False) -> None:
        for printer_id in printer_ids:
            driver = self._drivers.pop(printer_id, None)
            self._fingerprints.pop(printer_id, None)
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
