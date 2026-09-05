"""The Prometheus scrape over HTTP.

Four things are pinned here, and each is a way `/metrics` could look healthy while
lying.

*The exposition itself*: a 200, the library's own `Content-Type` — the version
parameter is part of the contract a scraper reads — and a body that **parses**
rather than one that contains a substring.

*ADR-0007, at the point of most consequence*: a printer the worker could not reach
publishes `1`, one it reached publishes `0`, and one it stopped hearing about
publishes **nothing at all**. Exported as `1`, an `unknown` pages somebody at three
in the morning about a machine nobody has looked at; exported as `0`, it hides one
that has been dead since morning. Both are numbers the farm never measured.

*The denominator*: with nothing published, the family carries no samples. The
roster is what the worker observed, never the `printers` table, so an uncollected
farm must not read as a farm with nothing offline.

*The money guard*, which is the irreversible-flavoured path in this endpoint: an
unauthenticated surface quietly starting to carry revenue. Written as a rule over
parsed metric names rather than a `not in` on the response text, so it goes on
holding when a fourth series is added.

`wal_archive_failures` is deliberately never asserted on: whether the machine the
suite runs on has a working `archive_command` is a property of that machine, which
is the same reason `test_health_api.py` leaves `wal_archiving` alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.fleet.models import Printer
from printorian.core import metrics
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.driver_health import CONNECTED, UNAVAILABLE, UNKNOWN, DriverHealth
from printorian.core.events import EventBus
from printorian.core.ids import EntityId, new_id
from printorian.core.storage import InMemoryObjectStore
from tests.conftest import wire_app


class _TestDatabase:
    """Stands in for `core.db.Database`, per the idiom the API tests use."""

    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, poolclass=NullPool)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


class _PublishedStates:
    """Stands in for the worker's published readings, which the API only reads."""

    def __init__(self, *drivers: DriverHealth) -> None:
        self._drivers = list(drivers)

    async def report(self) -> list[DriverHealth]:
        return self._drivers


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[_TestDatabase]:
    database = _TestDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    database: _TestDatabase,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    wire_app(
        app,
        settings=settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


def publish(client: AsyncClient, *drivers: DriverHealth) -> None:
    """Put readings where the endpoint looks for them.

    Reaching through the transport for the app because the fixture yields only the
    client — the same idiom `test_health_api.py` uses for the same reason.
    """
    client._transport.app.state.driver_states = _PublishedStates(*drivers)  # type: ignore[attr-defined]


async def add_printer(database: _TestDatabase, printer_id: EntityId, *, brand: str) -> None:
    """A real fleet row, so the brand label comes from the farm rather than a stub."""
    async for session in database.session():
        session.add(Printer(id=printer_id, name=f"P-{brand}", brand=brand))


def parse(body: str) -> list[tuple[str, dict[str, str], float]]:
    """The exposition as a scraper reads it: parsed, never pattern-matched."""
    return [
        (sample.name, dict(sample.labels), sample.value)
        for family in text_string_to_metric_families(body)
        for sample in family.samples
    ]


def samples_of(body: str, name: str) -> list[tuple[dict[str, str], float]]:
    return [(labels, value) for sample, labels, value in parse(body) if sample == name]


async def test_the_scrape_is_open_and_speaks_the_exposition_format(client: AsyncClient) -> None:
    """Unauthenticated, for the reason the health probes are: a scraper has no session.

    The content type comes from the library rather than from a string somebody
    typed — its `version` parameter is part of what a scraper reads to decide how
    to parse the body.
    """
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == metrics.CONTENT_TYPE
    # Parsing is the assertion: a body that a scraper cannot read is a broken
    # endpoint however much of it looks right by eye.
    parse(response.text)


async def test_a_measured_zero_is_published_as_a_zero(client: AsyncClient) -> None:
    """Partition headroom, and the half of ADR-0007 that is easy to lose.

    The suite's schema has only the `DEFAULT` partition, so the farm genuinely has
    no month in hand — the reading that should page somebody. It has to arrive as
    `0`, because a helper that published nothing whenever the answer was empty
    would pass every absence test in this file and arm no alert at all.
    """
    body = (await client.get("/metrics")).text

    assert samples_of(body, metrics.TELEMETRY_PARTITION_MONTHS_AHEAD) == [({}, 0.0)]


async def test_each_driver_state_publishes_the_reading_it_actually_is(
    client: AsyncClient, database: _TestDatabase
) -> None:
    """The case with the most consequence in this file.

    `unavailable` is an observed failure and publishes `1`. `connected` is an
    observed success and publishes `0`. `unknown` — the worker named the printer
    and then stopped publishing readings for it — publishes **no sample**, so the
    graph shows a gap and nothing pages. Either of the two numbers a naive
    exporter would put there is an invention with a cost.
    """
    dead, alive, silent = new_id(), new_id(), new_id()
    await add_printer(database, dead, brand="bambu")
    await add_printer(database, alive, brand="prusa")
    await add_printer(database, silent, brand="bambu")
    publish(
        client,
        DriverHealth(printer_id=str(dead), name="P1S-01", state=UNAVAILABLE),
        DriverHealth(printer_id=str(alive), name="P1S-02", state=CONNECTED),
        DriverHealth(printer_id=str(silent), name="P1S-03", state=UNKNOWN),
    )

    body = (await client.get("/metrics")).text

    readings = {
        labels["printer"]: (labels["brand"], value)
        for labels, value in samples_of(body, metrics.PRINTERS_OFFLINE)
    }
    # An exact equality rather than three separate membership checks: what has to
    # hold is that `silent` produced no sample, and an `assert x not in` would go
    # on passing if the whole family disappeared.
    assert readings == {str(dead): ("bambu", 1.0), str(alive): ("prusa", 0.0)}


async def test_an_unobserved_fleet_is_not_a_healthy_fleet(client: AsyncClient) -> None:
    """Nothing published means the family carries no samples at all.

    The denominator rule (root CLAUDE.md §1): the roster is what the *worker*
    observed, so a farm nobody is collecting from must not read as a farm with no
    printers offline. Reading the `printers` table to "make sure every machine is
    reported" is the mistake this pins shut.
    """
    publish(client)

    body = (await client.get("/metrics")).text

    assert samples_of(body, metrics.PRINTERS_OFFLINE) == []


async def test_a_printer_with_no_row_keeps_its_reading_and_loses_its_label(
    client: AsyncClient,
) -> None:
    """A retired or unknown id is still an observation.

    The measurement is real and it is the *decoration* that is missing, so the
    brand goes out empty rather than the sample being dropped. Dropping it would
    delete an observed failure over a missing label.
    """
    orphan = new_id()
    publish(client, DriverHealth(printer_id=str(orphan), name="?", state=UNAVAILABLE))

    body = (await client.get("/metrics")).text

    assert samples_of(body, metrics.PRINTERS_OFFLINE) == [
        ({"printer": str(orphan), "brand": ""}, 1.0)
    ]


async def test_no_money_ever_reaches_this_unauthenticated_surface(
    client: AsyncClient, database: _TestDatabase
) -> None:
    """`VIEW_FINANCIALS` has no way to be checked on an endpoint with no caller.

    INFRASTRUCTURE §5's tenth series is `printorian_sla_credit_accrued_rub`, and it
    stays out until the scrape has an identity. Stated as a rule over every parsed
    name rather than as a substring check on the body, so the guard still holds the
    day somebody adds a fourth collector without reading the docstring.
    """
    await add_printer(database, new_id(), brand="bambu")

    names = {name for name, _labels, _value in parse((await client.get("/metrics")).text)}

    assert not [name for name in names if name.endswith("_rub")]
    assert not [name for name in names if "sla_credit" in name]


async def test_the_fleet_occupancy_route_is_a_different_surface(client: AsyncClient) -> None:
    """The collision the issue warns about, pinned in one assertion.

    `/fleet/metrics` is the farm's measured occupancy in seconds behind
    `VIEW_PRODUCTION` — a screen's data, still refusing an unauthenticated caller —
    while `/metrics` above answers 200 to anyone. Neither path shadows the other.
    """
    response = await client.get("/fleet/metrics", params={"since": "2026-03-02T00:00:00Z"})

    assert response.status_code == 401
