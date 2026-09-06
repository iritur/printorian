"""The failure record over HTTP.

Four things are pinned here, and each is a way this could fail in production while
every unit test above still passed.

*Authorization*: reading reliability is `VIEW_PRODUCTION` and writing to the record
is `OPERATE_PRINTER` — production permissions, never the commercial one.

*The response key set*: seconds and counts go out and money does not. The kit draws
«ПОТЕРЯ 3 820 ₽» on this very panel and `Printer.amortization_per_hour` is on the
view the composition already holds, so the multiplication is one line away — and it
would put rubles behind a production gate.

*The unknown id*: recording a failure against a printer that does not exist is a
404 and not a row filed against nothing (ADR-0007).

*The nulls survive serialization*: an unrepaired failure reports `null` minutes,
not `0`. `Number(null)` is 0 in TypeScript and this is the last hop that can still
make the distinction impossible to lose.

The fixtures are `_fleet_metrics_support.py`'s — the same signed-in app and the same
`LAST_CLOSED`, because `/service/reliability` reuses `/fleet/metrics`' window rule
and a suite with a second idea of "the last closed hour" would be asserting on
buckets the endpoint refuses to serve.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore
from tests.api._fleet_metrics_support import (
    LAST_CLOSED,
    MetricsDatabase,
    auth,
    register,
    signed_in_app,
    since,
)


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[MetricsDatabase]:
    database = MetricsDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def client(
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    object_store: InMemoryObjectStore,
    database: MetricsDatabase,
) -> AsyncIterator[AsyncClient]:
    async with await signed_in_app(
        database, settings=settings, clock=clock, bus=bus, object_store=object_store
    ) as http:
        yield http


def a_failure(printer_id: object, **extra: object) -> dict[str, object]:
    """A body whose `detected_at` is inside every window these tests read.

    Defaulting it to the clock — which is what a person at a machine does — would
    put the failure at `FROZEN_NOW`, and the window is clamped to the last *closed*
    hour and excludes its own end. The failure would exist and never be counted.
    """
    return {"printer_id": str(printer_id), "detected_at": LAST_CLOSED.isoformat(), **extra}


# ------------------------------------------------------------- authorization


async def test_reliability_is_closed_to_anonymous_callers(client: AsyncClient) -> None:
    assert (await client.get("/service/reliability", params={"since": since(4)})).status_code == 401


async def test_reliability_is_refused_without_view_production(client: AsyncClient) -> None:
    buyer = await auth(client, "buyer@example.com")

    response = await client.get("/service/reliability", params={"since": since(4)}, headers=buyer)

    assert response.status_code == 403


async def test_an_operator_may_read_reliability_without_the_commercial_permission(
    client: AsyncClient,
) -> None:
    """The whole reason this route is `VIEW_PRODUCTION`: it emits no money.

    Both halves are asserted, because the pair is the claim: the operator is let in
    here and is still refused the screen that carries revenue.
    """
    operator = await auth(client, "op@example.com")

    allowed = await client.get("/service/reliability", params={"since": since(4)}, headers=operator)

    assert allowed.status_code == 200, allowed.text
    assert (await client.get("/dashboard", headers=operator)).status_code == 403


async def test_recording_a_failure_requires_a_production_role(client: AsyncClient) -> None:
    """A customer cannot file a failure against the farm's hardware.

    What this cannot yet distinguish is `OPERATE_PRINTER` from `VIEW_PRODUCTION`:
    every staff role in `identity/policies.py` that holds the second also holds the
    first, so there is no caller who can read this router and not write to it. The
    two gates are still declared separately, because the role map is the thing
    likelier to change.
    """
    buyer = await auth(client, "buyer@example.com")
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)

    refused = await client.post("/service/failures", json=a_failure(printer), headers=buyer)
    allowed = await client.post(
        "/service/failures", json=a_failure(printer), headers=await auth(client, "op@example.com")
    )

    assert refused.status_code == 403
    assert allowed.status_code == 201, allowed.text


# -------------------------------------------------------------- money and energy


async def test_reliability_carries_no_money_field(client: AsyncClient) -> None:
    """The prohibition as a test rather than only as three docstrings.

    Asserted as the whole key set rather than as a search for "cost", so a «потеря»
    field arriving under any spelling — rubles, grams of wasted filament, kilowatt
    hours — fails here and has to be argued for rather than merged.
    """
    schema = (await client.get("/openapi.json")).json()

    assert set(schema["components"]["schemas"]["ReliabilityRow"]["properties"]) == {
        "printer_id",
        "printer_name",
        "state",
        "failures",
        "closed_failures",
        "open_failures",
        "observed_seconds",
        "failures_per_1000_hours",
        "mttr_minutes",
    }
    assert set(schema["components"]["schemas"]["ReliabilityReport"]["properties"]) == {
        "window",
        "rows",
        "causes",
        "uncategorised",
        "printers_reporting",
        "printers_listed",
    }


# ------------------------------------------------------------------- the record


async def test_recording_a_failure_against_an_unknown_printer_is_404(
    client: AsyncClient,
) -> None:
    """ADR-0007's unknown-id rule, and the reason the route looks the printer up.

    `ServiceDesk.record` trusts the id it is handed, so without the registry check
    a typo'd id is either a 500 from the foreign key or — worse, if it happens to
    exist — a failure filed against the wrong machine.
    """
    operator = await auth(client, "op@example.com")

    response = await client.post("/service/failures", json=a_failure(new_id()), headers=operator)

    assert response.status_code == 404
    assert response.json()["code"] == "error.fleet.not_found"


async def test_a_failure_a_person_records_is_never_marked_as_the_drivers(
    client: AsyncClient,
) -> None:
    """«СООБЩИЛ ДРАЙВЕР» is a badge only the sweep can print.

    `origin` is not in the request body at all: it is `person` by construction on
    this route. A client that could claim the machine reported something would make
    the badge meaningless, and `origin` is what tells the sweep which failures it
    may close on its own.
    """
    operator = await auth(client, "op@example.com")
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)

    body = (
        await client.post(
            "/service/failures",
            json=a_failure(printer, origin="driver", note="сорвало филамент"),
            headers=operator,
        )
    ).json()

    assert body["origin"] == "person"
    assert body["cause"] is None
    assert body["restored_at"] is None


async def test_an_open_failure_reports_a_null_repair_time_rather_than_zero(
    client: AsyncClient,
) -> None:
    """Where the invented zero would actually get reintroduced.

    A machine that is broken right now has no repair time. `0` would say it was
    fixed instantly, and would be the most flattering number on the panel.
    """
    operator = await auth(client, "op@example.com")
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    await client.post("/service/failures", json=a_failure(printer), headers=operator)

    body = (
        await client.get("/service/reliability", params={"since": since(4)}, headers=boss)
    ).json()

    (row,) = body["rows"]
    assert row["failures"] == 1
    assert row["open_failures"] == 1
    assert row["mttr_minutes"] is None
    # And the other absence, in the same row: nothing was ever summarised for this
    # machine, so there is no denominator and therefore no rate.
    assert row["observed_seconds"] is None
    assert row["failures_per_1000_hours"] is None
    assert body["uncategorised"] == 1
    assert body["causes"] == []


async def test_a_restored_failure_reports_the_repair_the_farm_measured(
    client: AsyncClient,
) -> None:
    """The round trip, and the refusal that guards it.

    A second restore is refused rather than allowed to overwrite: `restored_at` is
    the single measurement of how long this machine was down.
    """
    operator = await auth(client, "op@example.com")
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    opened = (
        await client.post("/service/failures", json=a_failure(printer), headers=operator)
    ).json()
    repaired = {"restored_at": (LAST_CLOSED.replace(minute=30)).isoformat()}

    closed = await client.post(
        f"/service/failures/{opened['id']}/restore", json=repaired, headers=operator
    )
    again = await client.post(
        f"/service/failures/{opened['id']}/restore", json=repaired, headers=operator
    )

    assert closed.status_code == 200, closed.text
    assert closed.json()["restored_at"] is not None
    assert again.status_code == 422
    assert again.json()["code"] == "error.service.already_restored"


async def test_naming_a_cause_moves_the_failure_into_the_funnel(client: AsyncClient) -> None:
    """The route a driver-opened failure gets a cause by, exercised end to end.

    Before it is named the failure is `uncategorised`; after, it is one bar of
    «Причины отказов». The two counts are separate the whole way through, which is
    what stops "nobody looked" from being drawn as "it was something else".
    """
    operator = await auth(client, "op@example.com")
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    opened = (
        await client.post("/service/failures", json=a_failure(printer), headers=operator)
    ).json()

    named = await client.post(
        f"/service/failures/{opened['id']}/cause",
        json={"cause": "filament_break"},
        headers=operator,
    )
    body = (
        await client.get("/service/reliability", params={"since": since(4)}, headers=boss)
    ).json()

    assert named.status_code == 200, named.text
    assert body["uncategorised"] == 0
    assert body["causes"] == [{"cause": "filament_break", "failures": 1}]
