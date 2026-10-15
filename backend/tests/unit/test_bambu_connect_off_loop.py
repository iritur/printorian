"""The real Bambu driver connecting to a machine that never answers (issue #26).

Not a mock: paho, TLS and the driver's own connect, against a TCP listener on
this machine that completes the handshake in the kernel's backlog and then says
nothing — a printer switched on but wedged. What is pinned is that the event
loop keeps running while that attempt waits. It used not to: paho's connect and
the first-report wait ran on the loop, and every worker loop in the process —
scheduler, SLA, intake, heartbeats — froze for a minute per silent printer
(`tools/measure_driver_pool.py` measured 60.1 s for one, 240.2 s for four).

The handshake's bound is the MQTT keepalive, so the test shortens it to one
second; the property is the same at sixty, only slower to show.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator

import pytest

from printorian.core.clock import FixedClock
from printorian.drivers import DriverUnavailableError
from printorian.drivers.bambu import driver as bambu
from printorian.drivers.bambu import transport
from printorian.drivers.base import ConnectionInfo, ConnectionMode


@pytest.fixture
def silent_port(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    monkeypatch.setattr(transport, "MQTT_PORT", port)
    monkeypatch.setattr(bambu, "_KEEPALIVE_SECONDS", 1)
    yield port
    listener.close()


# paho's `Client._ssl_wrap_socket` does not close its `SSLSocket` when
# `do_handshake` times out; the socket is local to that method, so CPython frees it
# by reference count the moment the call unwinds, and the "unraisable" warning is
# that finalisation being reported. It is paho's, it predates issue #26, and the
# fix would be overriding a private method of a pinned dependency — a worse thing
# to own than a socket closed a frame late. Filtered for this test alone.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
async def test_a_silent_printer_does_not_freeze_the_event_loop(
    silent_port: int, clock: FixedClock
) -> None:
    info = ConnectionInfo(
        printer_id="silent",
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        serial="SILENT0000000",
        access_code="00000000",
    )
    driver = bambu.BambuDriver(info, clock)

    loop = asyncio.get_running_loop()
    stall = 0.0
    ticks = 0
    done = asyncio.Event()

    async def ticker() -> None:
        nonlocal stall, ticks
        last = loop.time()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = loop.time()
            stall = max(stall, now - last - 0.01)
            last = now
            ticks += 1

    tick = asyncio.create_task(ticker())
    began = loop.time()
    with pytest.raises(DriverUnavailableError):
        await driver.connect(info)
    took = loop.time() - began
    done.set()
    await tick

    # The attempt really did wait for the handshake to time out...
    assert took >= 0.9
    # ...and the loop went on running the whole time, not just before and after.
    assert stall < 0.5
    assert ticks > 30
