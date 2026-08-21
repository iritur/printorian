"""Prove the event relay carries an event between two processes.

Run inside a deployed container::

    python tools/relay_probe.py

The API and the workers are separate containers with an in-process event bus
each, so a live event raised by a sweep only reaches a watching console if the
Redis relay works *in this deployment* — right Redis URL, right channel, network
between the two. Every one of those is a property of the environment rather than
of the code, which is exactly the class of thing the release gate exists to catch
(docs/INFRASTRUCTURE.md §6).

So this stands in for the two processes: one relay subscribes, another publishes,
and the frame has to arrive. It exercises the same `EventRelay` the API and the
workers use, including the origin filter that keeps a publisher from receiving its
own echo.

Exit codes: ``0`` the frame arrived, ``1`` it did not.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from printorian.core.config import get_settings
from printorian.core.events import Event
from printorian.core.relay import EventRelay

#: Long enough for a round trip over a container network, short enough that a
#: broken relay fails the gate rather than hanging it.
TIMEOUT_SECONDS = 10.0


class _Probe(Event):
    """A live event, so it matches `LIVE_PATTERNS` and is actually relayed."""

    name = "fleet.printer_state_changed"


async def main() -> int:
    settings = get_settings()
    received: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def deliver(payload: dict[str, Any]) -> None:
        await received.put(payload)

    # Two relays with different origins: one process publishing, one subscribing.
    # Sharing an origin would prove nothing — the subscriber drops its own echo
    # on purpose, and the probe would time out for the right reason.
    listener = EventRelay(settings.redis_url, settings.events_channel, origin="probe-listener")
    publisher = EventRelay(settings.redis_url, settings.events_channel, origin="probe-publisher")

    await listener.start()
    await publisher.start()
    listener.listen(deliver)

    try:
        # The subscription is established asynchronously; a publish that races it
        # is simply dropped by Redis, since pub/sub keeps nothing. Retried rather
        # than slept once, so a slow container is not a failed gate.
        for _ in range(int(TIMEOUT_SECONDS)):
            await publisher.publish(_Probe())
            try:
                payload = await asyncio.wait_for(received.get(), timeout=1.0)
            except TimeoutError:
                continue
            print(f"relay ok: received {payload.get('name')} on {settings.events_channel}")
            return 0
        print(
            f"relay FAILED: nothing arrived on {settings.events_channel} "
            f"within {TIMEOUT_SECONDS:.0f}s",
            file=sys.stderr,
        )
        return 1
    finally:
        await listener.aclose()
        await publisher.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
