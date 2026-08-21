"""The live event stream.

Two things matter here: that an entitled client actually receives events, and that
an unentitled one never gets a socket at all. Live telemetry is production data.
"""

from __future__ import annotations

import asyncio
import gc
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from printorian.api.app import create_app
from printorian.api.ws import LIVE_PATTERNS, Hub
from printorian.contexts.fleet import events as fleet_events
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app

PASSWORD = "correct-horse-battery"

# The WebSocket tests drive an async app from a *synchronous* `TestClient`, which
# runs its own event loop and tears it down when the client exits. Connections
# opened inside that loop are finalised by the garbage collector rather than
# closed on it, and asyncpg reports the finalisation as an unraisable exception —
# which `filterwarnings = ["error"]` then attributes to whichever unrelated test
# happens to trigger the collection.
#
# Scoped to this module and no wider: it is an artefact of the sync-over-async
# harness these tests need, not a leak in the application. Everything else in the
# suite keeps warnings fatal, which is where real leaks would surface.
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


class _TestDatabase:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, poolclass=NullPool)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self):
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


@pytest.fixture
def app_client(
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
    clean_database: None,
):
    """A synchronous client, because WebSocket testing needs a real event loop."""
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)

    async def prepare() -> None:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session_factory() as session:
            identity = IdentityService(session, settings, clock, bus)
            for email, role in (
                ("op@example.com", Role.OPERATOR),
                ("buyer@example.com", Role.CUSTOMER),
            ):
                await identity.create_user(
                    CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
                )
            await session.commit()

    asyncio.run(prepare())

    wire_app(
        app,
        settings=settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )
    app.state.hub = Hub()
    app.state.hub.attach(bus)

    with TestClient(app) as client:
        yield client

    asyncio.run(database.dispose())
    # Force the collection *here*, inside the module that tolerates it. The
    # sync client's loop is gone and its connections are unreachable but not yet
    # finalised; left to chance, asyncpg's `__del__` fires during some unrelated
    # test in another file and fails that one instead.
    gc.collect()


def sign_in(client: TestClient, email: str) -> str:
    response = client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    token: str = response.json()["token"]
    return token


def test_an_entitled_client_receives_live_events(app_client: TestClient) -> None:
    token = sign_in(app_client, "op@example.com")

    with app_client.websocket_connect(
        "/ws/events", headers={"Authorization": f"Bearer {token}"}
    ) as socket:
        # Publishing on the app's own bus is what a request or the poller does.
        bus: EventBus = app_client.app.state.event_bus
        asyncio.run(
            bus.publish(
                fleet_events.PrinterStateChanged(
                    printer_id=new_id(),
                    printer_name="p1s-01",
                    from_state="idle",
                    to_state="printing",
                )
            )
        )
        message = socket.receive_json()

    assert message["name"] == "fleet.printer_state_changed"
    assert message["printer_name"] == "p1s-01"
    assert message["to_state"] == "printing"


def test_a_customer_is_refused_the_stream(app_client: TestClient) -> None:
    """Live production telemetry is not a public feed."""
    token = sign_in(app_client, "buyer@example.com")

    # Starlette raises on a rejected handshake; the type is an implementation
    # detail, the refusal is the contract.
    with (
        pytest.raises(Exception),  # noqa: B017
        app_client.websocket_connect("/ws/events", headers={"Authorization": f"Bearer {token}"}),
    ):
        pass


def test_an_anonymous_client_never_gets_a_socket(app_client: TestClient) -> None:
    with (
        pytest.raises(Exception),  # noqa: B017
        app_client.websocket_connect("/ws/events"),
    ):
        pass


def test_identity_events_are_not_broadcast() -> None:
    """Account activity has no business on a floor display."""
    assert not any(pattern.startswith("identity") for pattern in LIVE_PATTERNS)
    assert "fleet.*" in LIVE_PATTERNS
    assert "order.*" in LIVE_PATTERNS


def test_the_shop_floor_posts_are_forwarded() -> None:
    """Both boards claim to be live, and neither was.

    Each subscribes to its own prefix and neither prefix was in this tuple, so a
    task moving updated nobody's screen until they reloaded. Nothing failed —
    which is exactly why it went unnoticed, and why the claim is pinned here
    rather than left to whoever next opens the board on a quiet afternoon.
    """
    assert "postproduction.*" in LIVE_PATTERNS
    assert "packaging.*" in LIVE_PATTERNS


def test_every_forwarded_event_is_modelled_by_the_client() -> None:
    """An event nobody can parse is an event nobody reacts to.

    The two sides drifted once already: the bus grew to twenty-one event types
    and this socket forwarded four families of them, while `@printorian/events`
    still declared five names. Nothing failed, because an unmodelled event
    arrives as a bare envelope and is silently ignored — correct at runtime, and
    a terrible way to find out the contract moved.

    Asserted from this side because only this side can read both files. The
    client package targets the browser and has no business acquiring a
    filesystem dependency to check a contract; its own suite asserts the cheaper
    converse, that it models nothing this socket cannot send.

    Only *exact* patterns are checked. `order.*` legitimately covers names a
    client has no reason to narrow, so demanding full coverage of a wildcard
    would make every new event a failing test in another language.
    """
    types_ts = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "packages"
        / "events"
        / "src"
        / "types.ts"
    )
    if not types_ts.exists():  # pragma: no cover - backend-only checkouts
        pytest.skip("frontend not present in this checkout")

    modelled = set(re.findall(r"name:\s*'([a-z._]+)'", types_ts.read_text(encoding="utf-8")))
    assert modelled, "could not read any event names out of types.ts"

    for pattern in (p for p in LIVE_PATTERNS if not p.endswith("*")):
        assert pattern in modelled, (
            f"{pattern} is forwarded to clients but @printorian/events does not model it"
        )


async def test_the_hub_drops_events_for_a_client_that_stalls() -> None:
    """A stalled browser tab must not become an unbounded queue in the server."""
    hub = Hub()
    queue = hub.subscribe()

    for index in range(250):
        await hub.broadcast(
            fleet_events.PrinterUnreachable(
                printer_id=new_id(), printer_name=f"p-{index}", reason="offline"
            )
        )

    # Bounded, and the connection survives to be cleaned up normally.
    assert queue.qsize() <= 100
    hub.unsubscribe(queue)
    assert hub.client_count == 0


async def test_events_carry_their_name_and_timestamp() -> None:
    event = fleet_events.PrinterStateChanged(
        printer_id=new_id(), printer_name="p1", from_state="idle", to_state="printing"
    )
    payload = event.payload()

    assert payload["name"] == "fleet.printer_state_changed"
    assert datetime.fromisoformat(payload["occurred_at"]).tzinfo is not None
    assert payload["occurred_at"] <= datetime.now(UTC).isoformat()


# ------------------------------------------------- desktop console auth


def test_a_client_may_authenticate_with_a_subprotocol(app_client: TestClient) -> None:
    """No WebSocket client can set an Authorization header on the handshake, and
    the desktop console has no cookie because it is not same-origin. A token in a
    query string would land in proxy logs, so it travels as a subprotocol."""
    token = sign_in(app_client, "op@example.com")

    with app_client.websocket_connect(
        "/ws/events", subprotocols=["printorian.v1", f"bearer.{token}"]
    ) as socket:
        bus: EventBus = app_client.app.state.event_bus
        asyncio.run(
            bus.publish(
                fleet_events.PrinterStateChanged(
                    printer_id=new_id(),
                    printer_name="p1s-09",
                    from_state="idle",
                    to_state="printing",
                )
            )
        )
        assert socket.receive_json()["printer_name"] == "p1s-09"


def test_the_negotiated_subprotocol_never_echoes_the_token(app_client: TestClient) -> None:
    """Echoing the client's `bearer.<token>` entry would put the credential back
    on the wire in a response header."""
    token = sign_in(app_client, "op@example.com")

    with app_client.websocket_connect(
        "/ws/events", subprotocols=["printorian.v1", f"bearer.{token}"]
    ) as socket:
        accepted = socket.accepted_subprotocol
        assert accepted == "printorian.v1"
        assert token not in (accepted or "")


def test_a_bad_subprotocol_token_is_refused(app_client: TestClient) -> None:
    with (
        pytest.raises(Exception),  # noqa: B017
        app_client.websocket_connect(
            "/ws/events", subprotocols=["printorian.v1", "bearer.not-a-real-token"]
        ),
    ):
        pass


def test_a_customer_is_refused_over_a_subprotocol_too(app_client: TestClient) -> None:
    """The credential channel must not become a way around the permission check."""
    token = sign_in(app_client, "buyer@example.com")

    with (
        pytest.raises(Exception),  # noqa: B017
        app_client.websocket_connect(
            "/ws/events", subprotocols=["printorian.v1", f"bearer.{token}"]
        ),
    ):
        pass


def test_an_event_raised_in_another_process_reaches_a_client(app_client: TestClient) -> None:
    """The defect this socket had in production, and the fix for it.

    The API and the workers are separate containers with an in-process bus each,
    so a `fleet.printer_state_changed` raised by the telemetry poller never
    reached anybody watching: it was published onto the *worker's* bus and stopped
    there. The relay delivers those as payloads rather than as `Event` objects,
    because in this process they only ever existed as JSON — which is why the hub
    has `broadcast_payload` at all.
    """
    token = sign_in(app_client, "op@example.com")

    with app_client.websocket_connect(
        "/ws/events", headers={"Authorization": f"Bearer {token}"}
    ) as socket:
        hub: Hub = app_client.app.state.hub
        asyncio.run(
            hub.broadcast_payload(
                {
                    "name": "postproduction.task_raised",
                    "task_id": "t-1",
                    "order_number": "P-1042",
                }
            )
        )
        message = socket.receive_json()

    assert message["name"] == "postproduction.task_raised"
    assert message["order_number"] == "P-1042"


def test_the_amount_never_reaches_the_floor(app_client: TestClient) -> None:
    """The socket and the REST API disagreed about who may see money.

    Every holder of `VIEW_PRODUCTION` is entitled to this stream, while the API
    keeps `VIEW_FINANCIALS` deliberately separate from every production
    permission — so a settled payment was showing its amount to an operator the
    REST API would refuse. Redacted on the one path both sources share, so it
    holds for a locally raised event and a relayed one alike.
    """
    token = sign_in(app_client, "op@example.com")

    with app_client.websocket_connect(
        "/ws/events", headers={"Authorization": f"Bearer {token}"}
    ) as socket:
        hub: Hub = app_client.app.state.hub
        asyncio.run(
            hub.broadcast_payload(
                {"name": "payment.settled", "order_id": "o-1", "amount": "12400.00"}
            )
        )
        message = socket.receive_json()

    assert message["name"] == "payment.settled"
    assert "amount" not in message
    # Still useful: the floor learns the order is paid, which is what it needs.
    assert message["order_id"] == "o-1"
