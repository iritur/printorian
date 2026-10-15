"""How the pool retries machines that will not connect (issue #26).

A connect attempt against a machine that is on but not answering takes a minute,
and the telemetry loop runs every five seconds. Before this, every pass retried
every unreachable printer one after another — the pass *was* the reconnect storm.
What is pinned here:

* **a failed printer is not retried before its backoff**, and is after it;
* **the backoff doubles to a ceiling and is jittered**, so a farm-wide outage
  does not bring every machine back in the same second;
* **new connection details are tried at once** — that is somebody fixing it;
* **attempts run together, under a cap**, so a pass costs the slowest attempt
  rather than their sum, and never more than the cap at once;
* **two passes cannot both connect one printer** — the scheduler and the poller
  share the pool, and a machine with two MQTT sessions has each unaware of the
  other.
"""

from __future__ import annotations

import asyncio
import random
from datetime import timedelta

import pytest

from printorian.contexts.fleet import FleetService
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.secrets import SecretBox
from printorian.drivers import DriverUnavailableError
from printorian.workers.drivers import RETRY_CAP_SECONDS, RETRY_FIRST_SECONDS
from tests.unit._driver_pool_support import KEY, FakeDriver, a_printer, pool_building, pool_with


@pytest.fixture
def fleet(db_session: object, clock: FixedClock, bus: EventBus) -> FleetService:
    return FleetService(db_session, clock, bus, SecretBox(KEY))  # type: ignore[arg-type]


class Dead(FakeDriver):
    async def connect(self, info: object) -> None:
        self.connects += 1
        raise DriverUnavailableError("error.driver.unavailable")


class _Fixed(random.Random):
    """A jitter source that always draws the same number."""

    def __init__(self, draw: float) -> None:
        super().__init__()
        self._draw = draw

    def random(self) -> float:
        return self._draw


async def test_a_failed_printer_waits_for_its_backoff(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    printer = await a_printer(fleet)
    driver = Dead()
    # The top of the jitter range: the delay is the whole first step.
    pool = pool_with(monkeypatch, driver, clock, jitter=_Fixed(0.999))

    await pool.refresh(fleet, [printer])
    for _ in range(5):  # five telemetry passes, five seconds apart
        clock.advance(timedelta(seconds=5))
        await pool.refresh(fleet, [printer])
    assert driver.connects == 1

    clock.advance(timedelta(seconds=RETRY_FIRST_SECONDS))
    await pool.refresh(fleet, [printer])
    assert driver.connects == 2


async def test_the_backoff_doubles_to_its_ceiling(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    printer = await a_printer(fleet)
    driver = Dead()
    pool = pool_with(monkeypatch, driver, clock, jitter=_Fixed(0.999))

    gaps: list[int] = []
    waited = 0
    await pool.refresh(fleet, [printer])
    while driver.connects < 8:
        clock.advance(timedelta(seconds=1))
        waited += 1
        before = driver.connects
        await pool.refresh(fleet, [printer])
        if driver.connects > before:
            gaps.append(waited)
            waited = 0

    assert gaps[:4] == [30, 60, 120, 240]
    assert set(gaps[4:]) == {RETRY_CAP_SECONDS}


async def test_the_jitter_shortens_the_wait_by_up_to_half(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    """The bottom of the range: half the step, never less and never more."""
    printer = await a_printer(fleet)
    driver = Dead()
    pool = pool_with(monkeypatch, driver, clock, jitter=_Fixed(0.0))

    await pool.refresh(fleet, [printer])
    clock.advance(timedelta(seconds=RETRY_FIRST_SECONDS / 2 - 1))
    await pool.refresh(fleet, [printer])
    assert driver.connects == 1
    clock.advance(timedelta(seconds=1))
    await pool.refresh(fleet, [printer])
    assert driver.connects == 2


async def test_new_connection_details_are_tried_at_once(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    printer = await a_printer(fleet, host="10.0.0.5")
    driver = Dead()
    pool = pool_with(monkeypatch, driver, clock)
    await pool.refresh(fleet, [printer])

    printer.host = "10.0.0.6"
    await pool.refresh(fleet, [printer])

    assert driver.connects == 2


async def test_attempts_run_together_and_never_beyond_the_cap(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    printers = [await a_printer(fleet, name=f"P-{i}", host=f"10.0.0.{i + 10}") for i in range(6)]
    in_flight = 0
    most = 0

    class Slow(FakeDriver):
        async def connect(self, info: object) -> None:
            nonlocal in_flight, most
            in_flight += 1
            most = max(most, in_flight)
            await asyncio.sleep(0.2)
            in_flight -= 1
            raise DriverUnavailableError("error.driver.unavailable")

    pool = pool_building(monkeypatch, Slow, clock, connect_concurrency=3)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await pool.refresh(fleet, printers)
    took = loop.time() - began

    assert most == 3
    # Two waves of three, not six in a row: well under the 1.2 s of a serial pass.
    assert took < 0.8


async def test_two_passes_cannot_both_connect_one_printer(
    fleet: FleetService, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    printer = await a_printer(fleet)
    built: list[FakeDriver] = []

    class Slow(FakeDriver):
        async def connect(self, info: object) -> None:
            await asyncio.sleep(0.1)
            await super().connect(info)

    def build() -> FakeDriver:
        built.append(Slow())
        return built[-1]

    pool = pool_building(monkeypatch, build, clock)
    scheduler, telemetry = await asyncio.gather(
        pool.refresh(fleet, [printer]), pool.refresh(fleet, [printer])
    )

    assert len(built) == 1
    assert scheduler == telemetry == {str(printer.id): built[0]}
