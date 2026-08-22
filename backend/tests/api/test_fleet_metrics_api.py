"""The measured-history endpoints over HTTP.

Four things are pinned here, and each of them is a way the design could quietly
fail in production while every unit test still passed.

*Authorization*: production data, `VIEW_PRODUCTION` — an operator gets in, a
customer does not, and the routes never need the commercial permission `/dashboard`
carries, because they never emit money.

*Bounds*: the window is refused rather than shortened, with a code the client can
act on.

*The unknown id*: a 404 rather than a 200 carrying a dense grid of nothing.

*The nulls survive serialization*: the JSON says ``null``, not ``0``, for a
temperature nobody read and an hour nobody summarised. That is the last hop this
side of the wire can defend.

The fixtures are in `_fleet_metrics_support.py`, including why every window here is
cut against the last *closed* hour rather than against `now`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal

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
from tests.conftest import FROZEN_NOW
from tests.unit._measure_support import FULL_HOUR, summarised


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


# ------------------------------------------------------------- authorization


async def test_the_metrics_are_closed_to_anonymous_callers(client: AsyncClient) -> None:
    assert (await client.get("/fleet/metrics", params={"since": since(4)})).status_code == 401


async def test_a_customer_may_not_read_what_the_machines_did(client: AsyncClient) -> None:
    buyer = await auth(client, "buyer@example.com")

    response = await client.get("/fleet/metrics", params={"since": since(4)}, headers=buyer)

    assert response.status_code == 403


async def test_an_operator_may_read_the_metrics_without_the_commercial_permission(
    client: AsyncClient,
) -> None:
    """Seeing seconds is a production question, and seconds is all this emits.

    The moment it emitted rubles it would need `VIEW_FINANCIALS` — which is exactly
    why this test asserts *both* halves: the operator is let in here, and is still
    refused the screen that carries revenue.
    """
    operator = await auth(client, "op@example.com")

    allowed = await client.get("/fleet/metrics", params={"since": since(4)}, headers=operator)

    assert allowed.status_code == 200, allowed.text
    assert (await client.get("/dashboard", headers=operator)).status_code == 403


async def test_no_response_field_carries_money_or_energy(client: AsyncClient) -> None:
    """The prohibition, as a test rather than only as a docstring.

    `printing_seconds × amortization_per_hour` and `× nominal_power_kw` are each one
    multiplication from this payload. If a cost or a kilowatt ever appears on it,
    the endpoint has become a financial one wearing a production gate.
    """
    schema = (await client.get("/openapi.json")).json()
    fields = set(schema["components"]["schemas"]["FleetBucket"]["properties"])
    fields |= set(schema["components"]["schemas"]["PrinterBucket"]["properties"])

    assert not [name for name in fields if "cost" in name or "kw" in name or "amort" in name]


# -------------------------------------------------------------------- the shape


async def test_the_grid_is_dense_ascending_and_the_length_of_its_window(
    client: AsyncClient, database: MetricsDatabase
) -> None:
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    async with database.session_factory() as session:
        await summarised(session, printer, LAST_CLOSED, printing_seconds=FULL_HOUR)
        await session.commit()

    body = (await client.get("/fleet/metrics", params={"since": since(6)}, headers=boss)).json()

    assert len(body["buckets"]) == 6
    assert body["window"]["grain"] == "hour"
    assert body["latest_bucket"] is not None
    assert Decimal(body["buckets"][-1]["load"]) == Decimal(1)
    assert body["buckets"][-1]["printers_reporting"] == 1


async def test_an_unsummarised_hour_serializes_as_null_and_never_as_zero(
    client: AsyncClient, database: MetricsDatabase
) -> None:
    """Where the invented zero would actually get reintroduced.

    `Number(null)` is 0 in TypeScript and ``?? 0`` is one keystroke, so the wire has
    to make the distinction impossible to lose by accident: the field is absent of a
    value, not holding a small one.
    """
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    async with database.session_factory() as session:
        await summarised(session, printer, LAST_CLOSED, printing_seconds=FULL_HOUR)
        await session.commit()

    body = (await client.get("/fleet/metrics", params={"since": since(3)}, headers=boss)).json()

    gap = body["buckets"][0]
    assert gap["idle_seconds"] is None
    assert gap["observed_seconds"] is None
    assert gap["load"] is None
    assert gap["printers_reporting"] is None


async def test_a_machine_that_read_no_temperature_reports_null_not_a_cold_bed(
    client: AsyncClient, database: MetricsDatabase
) -> None:
    """The kit renders `hv-faint` «—» for this. ``0 °C`` would be a reading."""
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    async with database.session_factory() as session:
        await summarised(session, printer, LAST_CLOSED, printing_seconds=FULL_HOUR)
        await session.commit()

    body = (
        await client.get(f"/fleet/metrics/{printer}", params={"since": since(1)}, headers=boss)
    ).json()

    bucket = body["buckets"][0]
    assert bucket["nozzle_temp_avg_c"] is None
    assert bucket["bed_temp_max_c"] is None
    # The hour itself is real — which is what makes those two mean "not measured".
    assert Decimal(bucket["observed_seconds"]) == FULL_HOUR
    assert bucket["sample_count"] == 720
    assert bucket["error_codes"] == {}


async def test_the_two_routes_have_deliberately_different_shapes(
    client: AsyncClient, database: MetricsDatabase
) -> None:
    """The reason this is two routes and not one with a `printer_id` filter.

    Temperatures and codes mean something for one machine and nothing summed across
    fifty; `printers_reporting` is the other way round.
    """
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    async with database.session_factory() as session:
        await summarised(session, printer, LAST_CLOSED, printing_seconds=FULL_HOUR)
        await session.commit()

    farm = (await client.get("/fleet/metrics", params={"since": since(1)}, headers=boss)).json()
    one = (
        await client.get(f"/fleet/metrics/{printer}", params={"since": since(1)}, headers=boss)
    ).json()

    assert "printers_reporting" in farm["buckets"][0]
    assert "nozzle_temp_max_c" not in farm["buckets"][0]
    assert "printers_reporting" not in one["buckets"][0]
    assert "nozzle_temp_max_c" in one["buckets"][0]


async def test_a_total_is_one_bucket_stamped_at_the_windows_start(
    client: AsyncClient, database: MetricsDatabase
) -> None:
    """«Загрузка за 30 дней 71%» — the one figure the fleet popup asks for."""
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)
    async with database.session_factory() as session:
        await summarised(session, printer, LAST_CLOSED, printing_seconds=Decimal(1800))
        await summarised(
            session, printer, LAST_CLOSED - timedelta(hours=1), printing_seconds=FULL_HOUR
        )
        await session.commit()

    body = (
        await client.get(
            f"/fleet/metrics/{printer}",
            params={"since": since(4), "grain": "total"},
            headers=boss,
        )
    ).json()

    assert len(body["buckets"]) == 1
    assert body["buckets"][0]["bucket_start"] == body["window"]["since"]
    assert Decimal(body["buckets"][0]["load"]) == Decimal("0.75")


# ------------------------------------------------------------------- the bounds


async def test_a_grid_wider_than_the_ceiling_is_refused_with_the_ceiling_named(
    client: AsyncClient,
) -> None:
    boss = await auth(client, "boss@example.com")

    refused = await client.get("/fleet/metrics", params={"since": since(800)}, headers=boss)

    assert refused.status_code == 422
    assert refused.json()["code"] == "error.fleet.metrics.window_too_wide"
    assert refused.json()["details"]["max_hours"] == "744"


async def test_the_same_window_is_allowed_once_the_grain_is_a_total(
    client: AsyncClient,
) -> None:
    """The 744 cap is about the *grid*; a scalar over the same span is one row."""
    boss = await auth(client, "boss@example.com")
    params = {"since": since(800), "grain": "total"}

    assert (await client.get("/fleet/metrics", params=params, headers=boss)).status_code == 200


async def test_an_empty_window_is_refused(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")

    refused = await client.get(
        "/fleet/metrics", params={"since": FROZEN_NOW.isoformat()}, headers=boss
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "error.fleet.metrics.window_empty"


async def test_a_naive_since_is_refused_rather_than_assumed_utc(client: AsyncClient) -> None:
    boss = await auth(client, "boss@example.com")

    refused = await client.get(
        "/fleet/metrics", params={"since": "2026-03-01T09:00:00"}, headers=boss
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "error.fleet.metrics.naive_timestamp"


async def test_the_window_that_comes_back_is_the_one_that_was_used(
    client: AsyncClient,
) -> None:
    """Aligned and clamped, not echoed back as asked.

    A caller that cannot see the clamp cannot tell a farm with no data from a
    request whose right-hand edge was quietly moved.
    """
    boss = await auth(client, "boss@example.com")
    asked = (FROZEN_NOW + timedelta(days=2)).isoformat()

    body = (
        await client.get("/fleet/metrics", params={"since": since(4), "until": asked}, headers=boss)
    ).json()

    assert datetime.fromisoformat(body["window"]["until"]) == FROZEN_NOW
    assert len(body["buckets"]) == 4


# -------------------------------------------------------------- the unknown id


async def test_an_unknown_machine_is_a_404_and_not_a_grid_of_nothing(
    client: AsyncClient,
) -> None:
    """`metric_rollups.printer_id` is deliberately not a foreign key.

    Without the registry check a typo answers 200 with a dense all-null grid, which
    renders as "this machine did nothing" — the most expensive ADR-0007 violation in
    the design, because it looks like a successful response.
    """
    boss = await auth(client, "boss@example.com")

    refused = await client.get(
        f"/fleet/metrics/{new_id()}", params={"since": since(4)}, headers=boss
    )

    assert refused.status_code == 404
    assert refused.json()["code"] == "error.fleet.not_found"


async def test_the_registry_is_checked_before_the_window_is_read(
    client: AsyncClient,
) -> None:
    """A retired machine with real history still answers; only an unknown id 404s."""
    boss = await auth(client, "boss@example.com")
    printer = await register(client, boss)

    found = await client.get(f"/fleet/metrics/{printer}", params={"since": since(4)}, headers=boss)

    assert found.status_code == 200
    assert found.json()["printer_id"] == str(printer)
    assert all(bucket["observed_seconds"] is None for bucket in found.json()["buckets"])


async def test_the_metrics_route_does_not_collide_with_a_printer_id(
    client: AsyncClient,
) -> None:
    """The reason for the separate prefix, asserted rather than assumed.

    Under `/printers` the literal "metrics" would be matched against
    ``/printers/{printer_id}`` by declaration order. Here the registry surface and
    the history surface cannot reach each other's paths at all.
    """
    boss = await auth(client, "boss@example.com")

    assert (await client.get("/printers/metrics", headers=boss)).status_code == 422
    assert (
        await client.get("/fleet/metrics", params={"since": since(4)}, headers=boss)
    ).status_code == 200
