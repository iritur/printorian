"""Carrying events between processes.

The relay is the fix for a real production defect: the API and the workers run as
separate containers with an in-process bus each, so every event raised by a sweep
— telemetry, the SLA clock, the post-production and packaging boards — was
published into the worker and stopped there. The console's boards refetch on an
event and never on a timer, so they loaded once and then sat still.

These tests drive `EventRelay` against a stand-in for Redis: what is worth pinning
is the *protocol* — what goes on the wire, what comes off it, and which frames are
dropped — not that `redis-py` publishes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import pytest

from printorian.core.events import Event, EventBus
from printorian.core.relay import LIVE_PATTERNS, EventRelay, redacted


class _Settled(Event):
    name: ClassVar[str] = "payment.settled"


class _SignedIn(Event):
    name: ClassVar[str] = "identity.sign_in_succeeded"


class _FakeRedis:
    """Enough of `redis.asyncio.Redis` for the relay's two verbs."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, frame: str) -> None:
        self.published.append((channel, frame))

    async def aclose(self) -> None:
        return None


def _wire(relay: EventRelay, client: _FakeRedis) -> None:
    """Stand a fake client in for the one `start()` would dial."""
    # Reaching past the public surface on purpose: this is the seam a fake has
    # to use, and the alternative is a constructor parameter that exists only
    # for tests.
    relay._client = client  # type: ignore[assignment]


async def test_a_live_event_goes_on_the_wire_tagged_with_its_origin() -> None:
    relay = EventRelay("redis://unused", "printorian.events", origin="worker-1")
    client = _FakeRedis()
    _wire(relay, client)

    await relay.publish(_Settled())

    (channel, frame) = client.published[0]
    assert channel == "printorian.events"
    body = json.loads(frame)
    assert body["origin"] == "worker-1"
    assert body["payload"]["name"] == "payment.settled"


async def test_only_the_live_patterns_are_relayed() -> None:
    """Account activity has no business on a floor display."""
    bus = EventBus()
    relay = EventRelay("redis://unused", "printorian.events", origin="worker-1")
    client = _FakeRedis()
    _wire(relay, client)
    relay.attach(bus)

    await bus.publish(_SignedIn())
    assert client.published == []

    await bus.publish(_Settled())
    assert len(client.published) == 1


async def test_publishing_survives_redis_being_gone() -> None:
    """An event is a notification, not a transaction.

    A relay that raised would fail the sweep that emitted the event — a farm that
    stops printing because a fan-out channel is down.
    """

    class _Broken(_FakeRedis):
        async def publish(self, channel: str, frame: str) -> None:
            raise OSError("connection refused")

    relay = EventRelay("redis://unused", "printorian.events", origin="worker-1")
    _wire(relay, _Broken())

    await relay.publish(_Settled())  # does not raise


async def test_an_unstarted_relay_is_a_no_op() -> None:
    relay = EventRelay("redis://unused", "printorian.events")
    await relay.publish(_Settled())  # no client, no error


# ---------------------------------------------------------------- redaction


def test_the_amount_is_stripped_from_a_settled_payment() -> None:
    """The socket and the REST API disagreed about who may see money.

    Every holder of `VIEW_PRODUCTION` is entitled to this stream, while the API
    keeps `VIEW_FINANCIALS` deliberately separate from every production
    permission. A floor display needs to know a payment landed, not how much.
    """
    payload: dict[str, Any] = {
        "name": "payment.settled",
        "order_id": "o-1",
        "amount": "12400.00",
    }

    cleaned = redacted(payload)

    assert "amount" not in cleaned
    assert cleaned["order_id"] == "o-1"


def test_an_event_with_nothing_to_redact_is_passed_through_unchanged() -> None:
    payload = {"name": "fleet.printer_state_changed", "printer_id": "p-1"}

    assert redacted(payload) is payload


def test_every_live_pattern_is_a_prefix_somebody_publishes() -> None:
    """A pattern nobody emits is a stream a client waits on for ever.

    `attention.*` is the deliberate exception — a reserved prefix the dashboard's
    panel is meant to be the first publisher of — and it is named here so that
    staying unclaimed is a decision rather than an oversight.
    """
    assert "attention.*" in LIVE_PATTERNS
    assert "fleet.*" in LIVE_PATTERNS


# ---------------------------------------------------------------- inbound


class _Pubsub:
    """A pubsub handle that yields a fixed script of messages, then blocks."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self.subscribed: list[str] = []

    async def __aenter__(self) -> _Pubsub:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def listen(self) -> Any:
        for message in self._messages:
            yield message
        # The real one never ends; blocking here keeps the relay's retry loop
        # from spinning through the script again while the test asserts.
        await asyncio.Event().wait()


class _ListeningRedis(_FakeRedis):
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        super().__init__()
        self._messages = messages

    def pubsub(self) -> _Pubsub:
        return _Pubsub(self._messages)


def _frame(origin: str, name: str) -> dict[str, Any]:
    return {
        "type": "message",
        "data": json.dumps({"origin": origin, "payload": {"name": name}}).encode(),
    }


async def _delivered(messages: list[dict[str, Any]], *, origin: str) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []

    async def deliver(payload: dict[str, Any]) -> None:
        received.append(payload)

    relay = EventRelay("redis://unused", "printorian.events", origin=origin)
    _wire(relay, _ListeningRedis(messages))
    relay.listen(deliver)
    # One turn of the loop is enough for the scripted messages to drain.
    await asyncio.sleep(0.05)
    await relay.aclose()
    return received


async def test_an_event_from_another_process_arrives() -> None:
    received = await _delivered([_frame("worker-1", "fleet.printer_state_changed")], origin="api-1")

    assert [payload["name"] for payload in received] == ["fleet.printer_state_changed"]


async def test_a_process_does_not_receive_its_own_echo() -> None:
    """Both processes publish, and the API also attaches its hub to the local bus.

    Without the origin filter every event the API raised would reach its clients
    twice — once off the bus, once back through Redis.
    """
    received = await _delivered([_frame("api-1", "fleet.printer_state_changed")], origin="api-1")

    assert received == []


@pytest.mark.parametrize(
    "message",
    [
        {"type": "subscribe", "data": 1},
        {"type": "message", "data": b"not json at all"},
        {"type": "message", "data": json.dumps(["not", "an", "object"]).encode()},
    ],
)
async def test_a_frame_that_is_not_ours_is_dropped_not_fatal(message: dict[str, Any]) -> None:
    """The channel is shared infrastructure.

    One bad message must not end the subscription and take every console in the
    building offline with it.
    """
    received = await _delivered(
        [message, _frame("worker-1", "order.status_changed")], origin="api-1"
    )

    assert [payload["name"] for payload in received] == ["order.status_changed"]
