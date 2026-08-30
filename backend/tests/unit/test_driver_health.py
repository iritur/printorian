"""Publishing driver connection state across the process boundary.

The pool of printer connections lives in the worker; the endpoint that reports on
it lives in the API. What is tested here is the rule that makes that gap safe: a
printer the worker named and then stopped publishing readings for must report
`unknown`, never `ok` and never silently vanish. Anything else lets a machine
that has been unreachable for six hours look exactly like one that is fine.
"""

from __future__ import annotations

import json

from printorian.core.driver_health import (
    CONNECTED,
    UNAVAILABLE,
    UNKNOWN,
    DriverHealth,
    DriverStates,
)

PREFIX = "printorian:worker"


class FakeRedis:
    """Just enough Redis to answer the two reads `report` performs."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.values.get(key) for key in keys]


def states_over(values: dict[str, str]) -> DriverStates:
    states = DriverStates("redis://unused")
    states._client = FakeRedis(values)  # type: ignore[assignment]
    return states


def a_roster(*entries: tuple[str, str]) -> str:
    return json.dumps([{"id": printer_id, "name": name} for printer_id, name in entries])


async def test_a_printer_with_a_reading_is_reported_as_the_worker_left_it() -> None:
    states = states_over(
        {
            f"{PREFIX}:drivers": a_roster(("p1", "P1S-01"), ("p2", "P1S-02")),
            f"{PREFIX}:driver:p1": json.dumps(
                {"state": CONNECTED, "code": None, "since": "2026-03-02T09:00:00+00:00"}
            ),
            f"{PREFIX}:driver:p2": json.dumps(
                {
                    "state": UNAVAILABLE,
                    "code": "error.driver.unavailable",
                    "since": "2026-03-02T03:14:00+00:00",
                }
            ),
        }
    )

    report = await states.report()

    assert [driver.printer_id for driver in report] == ["p1", "p2"]
    # Named, not counted: "one driver down" cannot say which machine to go to.
    assert report[0].name == "P1S-01"
    assert report[0].state == CONNECTED
    assert report[1].state == UNAVAILABLE
    assert report[1].code == "error.driver.unavailable"
    assert report[1].since == "2026-03-02T03:14:00+00:00"


async def test_a_printer_whose_reading_has_lapsed_reports_unknown() -> None:
    """The case the roster's longer window exists to produce.

    The readings expire with the loop that writes them and the roster outlives
    them, so a worker that stops publishing leaves printers that are *named* and
    unaccounted for. Reporting nothing at all here would say the farm has no
    printers; reporting `ok` would be ADR-0007's exact prohibition.
    """
    states = states_over({f"{PREFIX}:drivers": a_roster(("p1", "P1S-01"))})

    report = await states.report()

    assert [(driver.name, driver.state) for driver in report] == [("P1S-01", UNKNOWN)]
    assert report[0].code is None


async def test_a_reading_that_cannot_be_parsed_is_treated_as_absent() -> None:
    """A key from an older version must not raise inside a health endpoint."""
    states = states_over(
        {
            f"{PREFIX}:drivers": a_roster(("p1", "P1S-01")),
            f"{PREFIX}:driver:p1": "not json at all",
        }
    )

    assert [driver.state for driver in await states.report()] == [UNKNOWN]


async def test_nothing_published_reports_nothing_rather_than_an_empty_farm() -> None:
    """No roster is "nobody has said anything", which is not "there are no printers".

    The distinction matters downstream: a caller that read this as a fleet size
    would be counting the roster instead of the farm, which is the denominator
    mistake root CLAUDE.md §1 is about.
    """
    assert await states_over({}).report() == []


async def test_a_store_that_was_never_dialled_claims_nothing() -> None:
    """A farm with no Redis loses the reporting, not the work."""
    assert await DriverStates("redis://unused").report() == []


async def test_the_roster_is_written_after_the_readings_it_names() -> None:
    """A reader catching the write half-done must never see a named printer with
    no reading — that is indistinguishable from a lapsed one, and would report a
    freshly connected machine as `unknown`."""
    written: list[tuple[str, str, int]] = []

    class RecordingPipeline:
        def set(self, key: str, value: str, *, ex: int) -> None:
            written.append((key, value, ex))

        async def execute(self) -> None:
            return None

    class RecordingRedis(FakeRedis):
        def pipeline(self) -> RecordingPipeline:
            return RecordingPipeline()

    states = DriverStates("redis://unused")
    states._client = RecordingRedis({})  # type: ignore[assignment]

    await states.publish(
        [DriverHealth(printer_id="p1", name="P1S-01", state=CONNECTED)], ttl_seconds=30
    )

    assert [key for key, _value, _ex in written] == [
        f"{PREFIX}:driver:p1",
        f"{PREFIX}:drivers",
    ]
    # The roster outlives the readings, or a silent worker erases both at once.
    assert written[-1][2] > written[0][2]
