"""WebSocket fan-out for live updates.

Subscribes to the in-process event bus and pushes each event to every connected
client. The floor display, the fleet table and the customer cabinet all read from
this one stream rather than polling, so "printer 7 finished" reaches a person in
the second it happens rather than on the next refresh.

**Authenticated like everything else.** A browser cannot set headers on a
WebSocket, so the session cookie is the credential here; the handshake is rejected
before the socket is accepted if the caller is not entitled to watch. Live telemetry
is production data, not a public feed.

**Single-process today.** The bus is in-process, so this fans out to clients of
*this* worker. Running more than one API process needs the Redis relay described in
ARCHITECTURE §8; until then the deployment is one process (ADR-0003), and this
comment is the reminder rather than a surprise in production.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from printorian.api.deps import SESSION_COOKIE
from printorian.contexts.identity import IdentityService, Permission
from printorian.core.errors import PrintorianError
from printorian.core.events import Event, EventBus

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["events"])

#: What a watching client is subscribed to. Deliberately explicit rather than "all":
#: identity events carry account activity and have no business on a floor display.
LIVE_PATTERNS = ("fleet.*", "order.*", "payment.settled", "attention.*")

#: Drop a client that cannot keep up rather than buffering without limit. A stalled
#: browser tab must not become an unbounded queue in the server.
_MAX_QUEUED = 100

#: Negotiated subprotocol. Echoed back so a client that offers it gets a definite
#: answer rather than having to guess whether the server understood.
SUBPROTOCOL = "printorian.v1"

#: How a non-browser client presents a bearer token.
#:
#: No WebSocket client — browser or Electron renderer — can set an ``Authorization``
#: header on the handshake, and the desktop console has no session cookie because it
#: is not same-origin with the API. The remaining options are a query parameter or a
#: subprotocol value; a token in a URL ends up in proxy and access logs, so it goes
#: here instead. It is never echoed back in the negotiated subprotocol.
_BEARER_PREFIX = "bearer."


class Hub:
    """Tracks connected clients and forwards events to them."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[dict[str, Any]]] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUED)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._clients.discard(queue)

    async def broadcast(self, event: Event) -> None:
        """Hand an event to every client, skipping any that has fallen behind."""
        payload = event.payload()
        for queue in list(self._clients):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # `event` is structlog's own key for the message; naming a field that
                # would collide with it and raise instead of logging.
                logger.warning("ws_client_slow_dropped_event", event_name=event.name)

    def attach(self, bus: EventBus) -> None:
        """Route the live patterns from the bus into this hub."""
        for pattern in LIVE_PATTERNS:
            bus.subscribe_pattern(pattern, self.broadcast)


@router.websocket("/ws/events")
async def events_socket(websocket: WebSocket) -> None:
    """Stream live events to an entitled client."""
    actor = await _authenticate(websocket)
    if actor is None:
        # Closed before accepting: an unauthenticated caller never gets a socket.
        await websocket.close(code=4401)
        return

    hub: Hub = websocket.app.state.hub
    queue = hub.subscribe()
    # Echo the plain protocol only. Returning the client's `bearer.<token>` entry
    # would put the credential back on the wire in a response header.
    offered = _offered_subprotocols(websocket)
    await websocket.accept(subprotocol=SUBPROTOCOL if SUBPROTOCOL in offered else None)

    # Bound before the try so the cleanup below cannot raise NameError and mask
    # whatever actually went wrong.
    reader: asyncio.Task[None] | None = None
    try:
        # A reader task exists only to notice the client going away; anything it
        # receives is ignored, because this stream is one-directional by design.
        reader = asyncio.create_task(_drain(websocket))
        while True:
            payload = await queue.get()
            if websocket.client_state is not WebSocketState.CONNECTED:
                break
            await websocket.send_text(json.dumps(payload, default=str))
            if reader.done():
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(queue)
        if reader is not None:
            reader.cancel()


async def _drain(websocket: WebSocket) -> None:
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while True:
            await websocket.receive_text()


def _offered_subprotocols(websocket: WebSocket) -> list[str]:
    raw = websocket.headers.get("sec-websocket-protocol", "")
    return [value.strip() for value in raw.split(",") if value.strip()]


def _credential(websocket: WebSocket) -> str:
    """The caller's token, however this kind of client is able to present it.

    Cookie first (the storefront), then an ``Authorization`` header (tests and any
    non-browser HTTP client that can set one), then the subprotocol (the desktop
    console). See ``_BEARER_PREFIX``.
    """
    token = websocket.cookies.get(SESSION_COOKIE, "")
    if token:
        return token

    header = websocket.headers.get("authorization", "")
    if header.startswith("Bearer "):
        return header.removeprefix("Bearer ").strip()

    for offered in _offered_subprotocols(websocket):
        if offered.startswith(_BEARER_PREFIX):
            return offered[len(_BEARER_PREFIX) :]
    return ""


async def _authenticate(websocket: WebSocket) -> object | None:
    """Resolve the caller from their credential, or return None.

    Uses its own database session: the usual request dependencies do not apply to
    a WebSocket handshake, and borrowing one would outlive the request that owned it.
    """
    token = _credential(websocket)
    if not token:
        return None

    state = websocket.app.state
    try:
        async for session in state.database.session():
            identity = IdentityService(session, state.settings, state.clock, state.event_bus)
            actor = await identity.resolve(token)
            return actor if actor.can(Permission.VIEW_PRODUCTION) else None
    except PrintorianError:
        return None
    return None
