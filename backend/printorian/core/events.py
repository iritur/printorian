"""In-process asynchronous event bus.

Contexts react to each other through events rather than by importing each other's
services. A handler that raises never prevents the other handlers from running and
never fails the publisher — an event is a notification, not a transaction.

Fan-out to WebSocket clients (Redis pub/sub) subscribes to this bus; it is not a
second mechanism.
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, TypeVar, cast

import structlog

from printorian.core.ids import EntityId, new_id

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base class for everything published on the bus.

    ``name`` is the routing key and the wire contract shared with the frontend.
    """

    name: ClassVar[str] = "event"

    event_id: EntityId = field(default_factory=new_id)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def payload(self) -> dict[str, Any]:
        """Serializable body for transport. Subclasses override as needed."""
        body = asdict(self)
        body["event_id"] = str(self.event_id)
        body["occurred_at"] = self.occurred_at.isoformat()
        body["name"] = self.name
        return body


E = TypeVar("E", bound=Event)

Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    """Routes events to handlers registered by type or by name pattern."""

    def __init__(self) -> None:
        self._by_type: dict[type[Event], list[Handler]] = {}
        self._by_pattern: list[tuple[str, Handler]] = []
        self._recorders: list[list[Event]] = []

    # -- registration ----------------------------------------------------

    def subscribe(self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        """Register ``handler`` for exactly ``event_type``."""
        self._by_type.setdefault(event_type, []).append(cast(Handler, handler))

    def subscribe_pattern(self, pattern: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        """Register ``handler`` for event names matching a glob, e.g. ``attention.*``."""
        self._by_pattern.append((pattern, handler))

    def clear(self) -> None:
        """Drop every subscription. Test hygiene only."""
        self._by_type.clear()
        self._by_pattern.clear()

    # -- publication -----------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Deliver ``event`` to all matching handlers concurrently.

        Handler failures are logged and swallowed: subscribers must not be able to
        break the operation that emitted the event.
        """
        for recorder in self._recorders:
            recorder.append(event)

        handlers = self._handlers_for(event)
        if not handlers:
            return

        results = await asyncio.gather(*(h(event) for h in handlers), return_exceptions=True)
        for handler, result in zip(handlers, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "event_handler_failed",
                    # Not `event=`: structlog reserves that key for the message
                    # itself, and the collision raises TypeError — which would
                    # swallow the very failure this line exists to report.
                    event_name=event.name,
                    event_id=str(event.event_id),
                    handler=getattr(handler, "__qualname__", repr(handler)),
                    exc_info=result,
                )

    async def publish_all(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)

    def _handlers_for(self, event: Event) -> list[Handler]:
        handlers: list[Handler] = []
        for event_type, registered in self._by_type.items():
            if isinstance(event, event_type):
                handlers.extend(registered)
        handlers.extend(
            handler
            for pattern, handler in self._by_pattern
            if fnmatch.fnmatchcase(event.name, pattern)
        )
        return handlers

    # -- testing ---------------------------------------------------------

    @asynccontextmanager
    async def collecting(self) -> AsyncIterator[list[Event]]:
        """Capture every event published inside the block."""
        recorded: list[Event] = []
        self._recorders.append(recorded)
        try:
            yield recorded
        finally:
            self._recorders.remove(recorded)


#: Process-wide bus. Injected explicitly in services; this is the composition default.
event_bus = EventBus()
