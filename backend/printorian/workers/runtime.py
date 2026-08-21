"""The shared machinery every worker loop needs.

Split out of `runner` when the relay and the heartbeat arrived and the module went
past the length gate: what belongs together here is *process-wide* — settings, the
database, the bus, the connections to Redis — and what belongs in `runner` is the
loops and their lifetimes. Keeping both in one file made the boundary between "set
up once" and "runs for ever" a matter of scrolling.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.core.clock import SystemClock
from printorian.core.config import Settings, get_settings
from printorian.core.cpu import CpuGate
from printorian.core.db import Database
from printorian.core.events import EventBus
from printorian.core.heartbeat import Heartbeat, ttl_for
from printorian.core.relay import EventRelay
from printorian.core.storage import build_object_store, prepare_root


class WorkerRuntime:
    """The shared machinery every worker needs: settings, database, bus, clock."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.clock = SystemClock()
        self.bus = EventBus()
        self.database = Database(self.settings)
        # Same store the API writes through. Proved usable here too: the worker
        # collects models, and a collector that cannot reach the disk should say
        # so at startup rather than silently collecting nothing.
        self.object_store = build_object_store(prepare_root(self.settings.storage_root))
        # Mesh measurement runs here rather than on the loop driving the printers.
        self.cpu = CpuGate(self.settings.cpu_workers)
        # Every event a sweep raises goes onto the relay as well as onto the local
        # bus. Without this the API never sees them — the two run as separate
        # containers — and the console's "live" boards are live only for what
        # somebody clicked (`core.relay`).
        self.relay = EventRelay(self.settings.redis_url, self.settings.events_channel)
        # What lets a wedged loop be told apart from a working one (`core.heartbeat`).
        self.heartbeat = Heartbeat(self.settings.redis_url)

    async def open(self) -> None:
        """Connect the things that talk to Redis, and start relaying events."""
        await self.heartbeat.start()
        if self.settings.events_relay_enabled:
            await self.relay.start()
            self.relay.attach(self.bus)

    async def record_beat(self, loop: str, interval_seconds: int) -> None:
        """Note that ``loop`` completed a pass.

        Recorded *after* the work, deliberately: a beat written at the top of a
        pass would report a loop that starts and then hangs as healthy for ever,
        which is the failure this signal exists to catch.
        """
        await self.heartbeat.beat(
            loop,
            at=self.clock.now().isoformat(),
            ttl_seconds=ttl_for(interval_seconds, self.settings.worker_stale_intervals),
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """One unit of work, committed on success."""
        async for session in self.database.session():
            yield session

    async def dispose(self) -> None:
        await self.relay.aclose()
        await self.heartbeat.aclose()
        await self.database.dispose()


__all__ = ["WorkerRuntime"]
