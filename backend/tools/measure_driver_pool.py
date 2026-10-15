"""Measure what unreachable printers cost the worker process (issue #26).

The real `DriverPool` and the real `BambuDriver` — paho, TLS and all — pointed at
printers that never answer, while a ticker on the same event loop records how
long the loop was frozen. No printer is needed, and no mock stands in for the
part being measured: the connect path is the production one, only the far end is
silent.

"Silent" is a TCP listener on this machine that completes the handshake in the
kernel's backlog and then never speaks — the TLS client waits for a server hello
that never comes, until paho's connect timeout gives up. That is the shape of a
printer that is switched on but wedged, and the upper bound of one that is
switched off (where the operating system's own connect timeout may be shorter).

    python tools/measure_driver_pool.py 1 4 8 16

prints, for each printer count, how long one `refresh` took, how long the pass
straight after it took, and the longest single stall of the event loop across
both. The stall is the number that matters:
every worker loop — scheduler, SLA, intake, heartbeats — shares that loop, so a
stall is time in which none of them ran.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from printorian.core.clock import SystemClock
from printorian.core.config import Settings
from printorian.drivers.bambu import transport
from printorian.drivers.base import ConnectionInfo, ConnectionMode
from printorian.workers.drivers import DriverPool


@dataclass
class _Printer:
    """The attributes `DriverPool` reads from a `fleet.models.Printer`."""

    name: str
    host: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    brand: str = "bambu"
    serial: str = "SILENT0000000"
    access_code_encrypted: str = "set"

    @property
    def connection_mode(self) -> Any:
        return ConnectionMode.LAN


class _Fleet:
    """The one `FleetService` method the pool calls."""

    def connection_for(self, printer: _Printer) -> ConnectionInfo:
        return ConnectionInfo(
            printer_id=str(printer.id),
            mode=ConnectionMode.LAN,
            host=printer.host,
            serial=printer.serial,
            access_code="00000000",
        )


def _silent_listener() -> socket.socket:
    """Accepts connections into the backlog and never reads or writes."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1024)
    return listener


async def _measure(count: int, pool: DriverPool) -> tuple[float, float, float]:
    printers = [_Printer(name=f"P-{index:02d}", host="127.0.0.1") for index in range(count)]
    stall = 0.0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal stall
        interval = 0.02
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(interval)
            now = time.perf_counter()
            stall = max(stall, now - last - interval)
            last = now

    tick = asyncio.create_task(ticker())
    await asyncio.sleep(0.1)
    began = time.perf_counter()
    connected = await pool.refresh(_Fleet(), printers)  # type: ignore[arg-type]
    took = time.perf_counter() - began
    # The very next pass, as the telemetry loop would run it five seconds later.
    began = time.perf_counter()
    await pool.refresh(_Fleet(), printers)  # type: ignore[arg-type]
    again = time.perf_counter() - began
    stop.set()
    await tick
    assert connected == {}, "a silent printer must not count as connected"
    await pool.aclose()
    return took, again, stall


async def main(counts: list[int]) -> None:
    listener = _silent_listener()
    transport.MQTT_PORT = listener.getsockname()[1]  # type: ignore[misc]
    settings = Settings(secret_key="a-development-secret-key-not-for-production")
    print(f"{'printers':>8}  {'refresh s':>9}  {'next pass s':>11}  {'longest stall s':>15}")
    for count in counts:
        took, again, stall = await _measure(count, DriverPool(SystemClock(), settings))
        print(f"{count:>8}  {took:>9.1f}  {again:>11.1f}  {stall:>15.1f}")
    listener.close()


if __name__ == "__main__":
    asyncio.run(main([int(arg) for arg in sys.argv[1:]] or [1, 4, 8]))
