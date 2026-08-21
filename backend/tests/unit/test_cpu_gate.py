"""The gate that keeps blocking work off the event loop, and bounded.

What matters is not that `CpuGate.run` returns the right answer — `asyncio.to_thread`
does that. It is that the loop keeps running while the work does, and that a flood
of callers cannot occupy an unbounded number of threads.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from printorian.core.cpu import CpuGate


def test_a_limit_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        CpuGate(0)


async def test_the_work_runs_off_the_event_loop() -> None:
    """The whole point: the calling thread is not the one that blocks."""
    gate = CpuGate(2)
    caller = threading.get_ident()

    worker = await gate.run(threading.get_ident)

    assert worker != caller


async def test_the_loop_keeps_serving_while_the_work_runs() -> None:
    """A second coroutine makes progress during a blocking call.

    This is the regression: mesh analysis used to run inline in the request
    handler, so for its whole duration the process served nothing — no other
    quote, no health check, no WebSocket frame.
    """
    gate = CpuGate(2)
    ticks = 0

    async def keep_ticking() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.001)

    ticker = asyncio.create_task(keep_ticking())
    try:
        await gate.run(time.sleep, 0.2)
    finally:
        ticker.cancel()

    assert ticks > 1, "the event loop was blocked for the whole call"


async def test_no_more_than_the_limit_run_at_once() -> None:
    """Past the limit, callers queue rather than each taking a thread.

    Unbounded `to_thread` turns a burst of large uploads into memory exhaustion:
    every concurrent analysis holds its mesh plus its intermediate arrays.
    """
    gate = CpuGate(2)
    concurrent = 0
    peak = 0
    guard = threading.Lock()

    def work() -> None:
        nonlocal concurrent, peak
        with guard:
            concurrent += 1
            peak = max(peak, concurrent)
        time.sleep(0.05)
        with guard:
            concurrent -= 1

    await asyncio.gather(*(gate.run(work) for _ in range(8)))

    assert peak <= 2
    assert gate.in_flight == 0


async def test_a_slot_is_released_when_the_work_raises() -> None:
    """Otherwise one bad mesh permanently narrows the pool."""
    gate = CpuGate(1)

    def explode() -> None:
        raise ValueError("unreadable mesh")

    with pytest.raises(ValueError, match="unreadable mesh"):
        await gate.run(explode)

    assert gate.in_flight == 0
    assert await gate.run(lambda: "still works") == "still works"
