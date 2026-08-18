"""Phase 0 exit criterion: a virtual farm runs a full print cycle in CI.

Later phases extend this file as the real pipeline lands — the scheduler in Phase 4,
post-production in Phase 5 — so the end-to-end assertion grows with the product
instead of being written once the product is already too big to test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.units import Duration
from printorian.drivers import MockBehaviour, PrinterState
from tests.virtualfarm.farm import PrinterSpec, VirtualFarm, plate, two_hour_print

FARM_SIZE = 5


@pytest.fixture
async def farm(clock: FixedClock, settings: Settings, bus: EventBus) -> VirtualFarm:
    farm = VirtualFarm.of_size(FARM_SIZE, clock, settings, bus, two_hour_print())
    await farm.connect_all()
    return farm


async def test_farm_starts_with_every_printer_idle(farm: VirtualFarm) -> None:
    assert len(farm.printer_ids) == FARM_SIZE
    assert await farm.idle_printers() == farm.printer_ids
    assert farm.unreachable == set()


async def test_single_print_runs_to_completion(farm: VirtualFarm, clock: FixedClock) -> None:
    handle = await farm.dispatch("vp-00", plate())
    assert handle.value

    mid = await farm.advance(timedelta(hours=1))
    printing = next(t for t in mid if t.printer_id == "vp-00")
    assert printing.state is PrinterState.PRINTING
    assert printing.progress_percent == 50
    assert printing.layer_current == 200
    assert printing.remaining is not None
    assert printing.remaining.minutes == 60

    end = await farm.advance(timedelta(hours=1))
    finished = next(t for t in end if t.printer_id == "vp-00")
    assert finished.state is PrinterState.FINISHED


async def test_whole_farm_prints_concurrently_and_settles(farm: VirtualFarm) -> None:
    for printer_id in farm.printer_ids:
        await farm.dispatch(printer_id, plate(f"{printer_id}.3mf"))

    assert await farm.idle_printers() == []

    final = await farm.run_until_settled()
    assert {t.state for t in final} == {PrinterState.FINISHED}
    assert len(final) == FARM_SIZE


async def test_a_finished_printer_is_not_offered_as_available_capacity(
    farm: VirtualFarm,
) -> None:
    """The scheduler must not count a machine with a part still on its bed.

    Until an operator clears the plate (scenario step 10), that printer is occupied
    even though it is not printing.
    """
    await farm.dispatch("vp-00", plate())
    await farm.run_until_settled()

    assert "vp-00" not in await farm.idle_printers()


async def test_every_observation_is_published_as_an_event(farm: VirtualFarm, bus: EventBus) -> None:
    await farm.dispatch("vp-01", plate())

    async with bus.collecting() as events:
        await farm.advance(timedelta(minutes=30))

    assert len(events) == FARM_SIZE
    assert {e.name for e in events} == {"fleet.telemetry_observed"}
    printing = [e for e in events if getattr(e, "printer_id", None) == "vp-01"]
    assert printing[0].state is PrinterState.PRINTING  # type: ignore[attr-defined]


async def test_a_failing_print_surfaces_and_does_not_stall_the_farm(
    clock: FixedClock, settings: Settings, bus: EventBus
) -> None:
    farm = VirtualFarm(
        clock,
        settings,
        bus,
        [
            PrinterSpec(printer_id="good", behaviour=two_hour_print()),
            PrinterSpec(
                printer_id="doomed",
                behaviour=MockBehaviour(print_duration=Duration.from_hours(2), fail_at_percent=40),
            ),
        ],
    )
    await farm.connect_all()
    await farm.dispatch("good", plate())
    await farm.dispatch("doomed", plate())

    final = await farm.run_until_settled()
    by_id = {t.printer_id: t for t in final}

    assert by_id["good"].state is PrinterState.FINISHED
    assert by_id["doomed"].state is PrinterState.ERROR
    assert by_id["doomed"].error_code == "mock.injected_failure"


async def test_unreachable_printer_is_marked_offline_and_contributes_no_telemetry(
    clock: FixedClock, settings: Settings, bus: EventBus
) -> None:
    """The V1 regression test: a dead printer must produce nothing, not fiction."""
    farm = VirtualFarm(
        clock,
        settings,
        bus,
        [
            PrinterSpec(printer_id="alive", behaviour=two_hour_print()),
            PrinterSpec(printer_id="dead", behaviour=MockBehaviour(unreachable=True)),
        ],
    )
    await farm.connect_all()

    assert farm.unreachable == {"dead"}
    assert await farm.idle_printers() == ["alive"]

    observations = await farm.poll()
    assert [t.printer_id for t in observations] == ["alive"]


async def test_a_printer_that_rejects_jobs_is_not_silently_treated_as_working(
    clock: FixedClock, settings: Settings, bus: EventBus
) -> None:
    from printorian.drivers import DriverRejectedError

    farm = VirtualFarm(
        clock,
        settings,
        bus,
        [PrinterSpec(printer_id="grumpy", behaviour=MockBehaviour(reject_jobs=True))],
    )
    await farm.connect_all()

    with pytest.raises(DriverRejectedError):
        await farm.dispatch("grumpy", plate())
