"""Carrying events between processes, so "live" screens are live.

The event bus is in-process (`core.events`), and the deployment runs the API and
the workers as **separate containers** (`deploy/compose.prod.yml`). Those two facts
together had a consequence nothing recorded: every event raised by a *sweep* rather
than by a request was published onto the worker's bus and stopped there.

That is almost everything the farm does on its own —
``fleet.printer_state_changed`` from the telemetry poller,
``postproduction.task_raised`` and ``packaging.parcel_raised`` from their sweeps,
``order.sla_credit_accrued`` from the SLA clock. The console's fleet board refetches
on an event and on connect and never on a timer, so in production it loaded once
and then sat there while printers started and finished. The boards were live only
for what a person had just clicked.

This is the Redis relay ARCHITECTURE §8 always described, built:

* **Every process publishes.** A process that raises a live event puts it on the
  channel, tagged with the id of the process that raised it.
* **The API subscribes** and fans what arrives out to its WebSocket clients,
  *skipping its own origin* — its local events already reached the hub directly
  through the bus. That is what keeps one event from arriving twice, and it is why
  this works unchanged whether Redis is present or not: without it, local events
  still reach local clients and only the cross-process hop is missing.

Redis is a transport here and not a source of truth (ARCHITECTURE §2). Nothing is
persisted, nothing is replayed, and a dropped frame is covered by the client's
resync-on-connect. So a relay that cannot reach Redis logs and keeps serving rather
than failing the operation that emitted the event — the same contract `EventBus`
gives its handlers, for the same reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Final

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

from printorian.core.events import Event, EventBus
from printorian.core.ids import new_id

logger = structlog.get_logger(__name__)

#: What a watching client is subscribed to, and therefore what is worth relaying.
#:
#: Deliberately not "everything": identity events carry account activity and have
#: no business on a floor display, and `job.*` / `plate.*` are internal steps that
#: no screen models yet — advertising a stream nothing renders costs bandwidth and
#: invites clients to depend on a shape that has not been designed.
#:
#: Lives in `core` rather than in `api.ws` because both the API and the workers
#: need it now, and they are siblings that may not import each other (`.importlinter`).
LIVE_PATTERNS: Final = (
    "fleet.*",
    "order.*",
    "payment.settled",
    "attention.*",
    "postproduction.*",
    "packaging.*",
)

#: Fields stripped from a payload before it reaches a WebSocket client, by event.
#:
#: `payment.settled` carries `amount`, and every holder of `VIEW_PRODUCTION` is
#: entitled to this socket — while the REST API keeps `VIEW_FINANCIALS`
#: deliberately separate from every production permission. The socket and the API
#: disagreed about who may see money; the socket was the one that was wrong, since
#: what a floor display needs from that event is *that a payment landed*, not how
#: much. The hub has no per-actor filtering, so this is a redaction rather than a
#: gate — a screen that should show money asks the API, which checks the permission.
REDACTED_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "payment.settled": ("amount",),
}

#: How long to wait before reconnecting a dropped subscription, in seconds.
_RECONNECT_DELAY = 2.0


def redacted(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``payload`` without the fields this event must not broadcast."""
    fields = REDACTED_FIELDS.get(str(payload.get("name", "")))
    if not fields:
        return payload
    return {key: value for key, value in payload.items() if key not in fields}


class EventRelay:
    """Fans live events between processes over Redis pub/sub."""

    def __init__(self, url: str, channel: str, *, origin: str | None = None) -> None:
        self._url = url
        self._channel = channel
        #: Identifies *this* process on the wire, so the subscriber can tell a
        #: relayed event from the echo of one it published itself.
        self.origin = origin or str(new_id())
        self._client: aioredis.Redis | None = None
        self._task: asyncio.Task[None] | None = None
        #: Publish failures are logged once per outage rather than per event: a
        #: five-second telemetry poll across a fifty-machine farm would otherwise
        #: write ten lines a second for as long as Redis is down.
        self._publish_failing = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._client = aioredis.from_url(self._url)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> bool:
        """Whether Redis answers. For the readiness endpoint."""
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except (RedisError, OSError):
            return False

    # -- outbound ----------------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        """Publish every live event this process raises onto the channel."""
        for pattern in LIVE_PATTERNS:
            bus.subscribe_pattern(pattern, self.publish)

    async def publish(self, event: Event) -> None:
        """Put one event on the channel. Never raises into the emitter."""
        if self._client is None:
            return
        frame = json.dumps(
            {"origin": self.origin, "payload": event.payload()},
            default=str,
        )
        try:
            await self._client.publish(self._channel, frame)
        except (RedisError, OSError) as failure:
            if not self._publish_failing:
                self._publish_failing = True
                logger.error("event_relay_publish_failed", error=str(failure))
            return
        if self._publish_failing:
            self._publish_failing = False
            logger.info("event_relay_publish_recovered")

    # -- inbound -----------------------------------------------------------

    def listen(self, deliver: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """Start forwarding events *from other processes* into ``deliver``."""
        self._task = asyncio.create_task(self._listen_forever(deliver), name="event-relay")

    async def _listen_forever(self, deliver: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """Subscribe, and keep subscribing.

        A relay that gave up on the first dropped connection would leave every
        console in the building silently stale — the exact failure this module
        exists to remove — so a broken subscription is retried rather than fatal.
        """
        while True:
            try:
                await self._consume(deliver)
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError) as failure:
                logger.warning("event_relay_subscription_lost", error=str(failure))
            except Exception:
                logger.exception("event_relay_subscription_failed")
            await asyncio.sleep(_RECONNECT_DELAY)

    async def _consume(self, deliver: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        if self._client is None:
            raise RuntimeError("EventRelay.start() must be awaited before listening")

        async with self._client.pubsub() as pubsub:
            await pubsub.subscribe(self._channel)
            logger.info("event_relay_subscribed", channel=self._channel)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                frame = _decode(message.get("data"))
                if frame is None or frame.get("origin") == self.origin:
                    # Our own echo. The local bus already handed this to the hub.
                    continue
                payload = frame.get("payload")
                if isinstance(payload, dict):
                    await deliver(payload)


def _decode(data: object) -> dict[str, Any] | None:
    """Parse one frame, or None if it is not one of ours.

    A malformed frame drops rather than killing the subscription: this channel is
    shared infrastructure, and one bad message must not take every console in the
    building offline.
    """
    if isinstance(data, bytes | bytearray):
        data = bytes(data).decode("utf-8", errors="replace")
    if not isinstance(data, str):
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        logger.warning("event_relay_undecodable_frame")
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ["LIVE_PATTERNS", "REDACTED_FIELDS", "EventRelay", "redacted"]
