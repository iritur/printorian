"""Running blocking CPU work without stopping the process that serves everything.

The API is one asyncio event loop in one process (ADR-0003, and `uvicorn` with a
single worker in the Dockerfile). Anything that computes for a second inside a
request handler is not slow for that caller — it is a second in which the process
serves *nothing*: no other quote, no `/health`, no WebSocket frame, and no console
request from the farm floor.

Mesh analysis is exactly that shape. `analyse_stl` is fully vectorised and still
takes seconds on a large model, because the manifold check sorts a few million
edges. Measured on a developer machine:

===============  ==========  ==========
triangles        file        wall clock
===============  ==========  ==========
50 000           2.5 MB      0.40 s
200 000          10 MB       1.48 s
400 000          20 MB       3.17 s
===============  ==========  ==========

Worst case is a *20 MB* upload rather than a 200 MB one — just under
`mesh._MANIFOLD_CHECK_LIMIT`, where the expensive check still runs. And
``POST /pricing/quote`` takes an optional actor, so that cost is reachable without
signing in.

So this gate does two things, and needs both:

* **Off the loop.** The call runs in a worker thread, so the loop keeps serving.
  NumPy releases the GIL for the array work, so the threads genuinely overlap.
* **Bounded.** Threads are not free — each analysis holds the mesh plus its
  intermediate arrays, and an unbounded `to_thread` turns a flood of uploads into
  a memory-exhaustion path instead of a stall. Past the limit callers *queue*,
  which is the honest behaviour: work is refused by the rate limiter at the edge,
  not by the machine falling over.

The limit is injected (ADR-0010), never read from a module-level constant here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class CpuGate:
    """Runs blocking work in a bounded pool of worker threads."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("CpuGate limit must be at least 1")
        self._limit = limit
        # Created eagerly, which is safe: since 3.10 `asyncio.Semaphore` binds to
        # the running loop on first use rather than at construction, so a gate
        # built during import or in a settings object works in whichever loop
        # ends up awaiting it.
        self._slots = asyncio.Semaphore(limit)
        self._in_flight = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        """Calls occupying a slot right now. For tests and for a metrics reading."""
        return self._in_flight

    async def run(self, work: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Run ``work`` in a worker thread, waiting for a slot first.

        Cancellation propagates the ordinary way while *waiting*; a call already
        running in a thread cannot be interrupted, because Python threads cannot
        be killed. That is a property of the work, not of this gate — the ceiling
        on how long one can hold a slot is the mesh size limit.
        """
        async with self._slots:
            self._in_flight += 1
            try:
                return await asyncio.to_thread(work, *args, **kwargs)
            finally:
                self._in_flight -= 1


__all__ = ["CpuGate"]
