"""Whether the worker loops are still sweeping — honestly.

`deploy/compose.prod.yml` disables the workers container's healthcheck, and the
comment beside it is right about why: the image's check curls the API, the workers
serve no HTTP, and a process check "passes for a worker deadlocked mid-sweep — a
liveness signal that cannot distinguish working from wedged is worse than none".
The release gate can only assert the container is still running.

This is the signal that *can* tell the difference. Each loop records a beat at the
end of every pass, so a beat means "this loop completed work", not "this process
exists". A loop that is blocked on a lock, stuck in a driver call, or throwing on
every iteration stops beating while its process stays perfectly alive.

**Kept in Redis, with an expiry.** Not because Redis is a source of truth — it is
not, and ARCHITECTURE §2 is explicit about that — but because a liveness signal is
the one kind of state that *should* evaporate on its own. The expiry is the check:
a key that is gone is a loop that has not swept within its window, and no sweeper,
no query and no clock comparison is needed to reach that conclusion. Writing it to
PostgreSQL instead would mean a table whose rows are only ever meaningful for a few
seconds, plus a job to tidy them.

A farm with no Redis loses the reporting, not the work: `beat` fails quietly, and
the report says `unknown` rather than inventing an answer (ADR-0007's rule applied
to the workers themselves).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

#: The loops `printorian.workers` runs. Named here so the health report can say
#: "postproduction is not beating" rather than only "one of them is not".
LOOPS: Final = ("scheduler", "telemetry", "sla", "postproduction", "packaging", "maintenance")

#: Floor on a key's lifetime, in seconds. The telemetry poller sweeps every five
#: seconds, so its window would otherwise be short enough that one slow pass over a
#: fifty-machine farm reads as an outage.
_MIN_TTL_SECONDS = 30


@dataclass(frozen=True, slots=True)
class LoopHealth:
    """What is known about one loop right now."""

    loop: str
    #: ``beating`` — swept within its window. ``stale`` — has not.
    #: ``unknown`` — the store could not be read, so nothing is claimed.
    state: str
    last_beat: str | None = None

    @property
    def is_healthy(self) -> bool:
        # `unknown` is not healthy and not a failure either: it is reported as it
        # is, and the caller decides. A healthcheck that treats "cannot tell" as
        # "fine" is the failure mode this module exists to remove.
        return self.state == "beating"


def ttl_for(interval_seconds: int, stale_intervals: int) -> int:
    """How long a beat stays valid: the loop's own interval, times its tolerance."""
    return max(_MIN_TTL_SECONDS, interval_seconds * stale_intervals)


class Heartbeat:
    """Records and reads the worker loops' beats."""

    def __init__(self, url: str, *, prefix: str = "printorian:worker") -> None:
        self._url = url
        self._prefix = prefix
        self._client: aioredis.Redis | None = None
        self._failing = False

    async def start(self) -> None:
        self._client = aioredis.from_url(self._url)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _key(self, loop: str) -> str:
        return f"{self._prefix}:{loop}"

    async def beat(self, loop: str, *, at: str, ttl_seconds: int) -> None:
        """Record that ``loop`` finished a pass. Never raises into the sweep."""
        if self._client is None:
            return
        try:
            await self._client.set(self._key(loop), at, ex=ttl_seconds)
        except (RedisError, OSError) as failure:
            if not self._failing:
                self._failing = True
                logger.warning("heartbeat_write_failed", loop=loop, error=str(failure))
            return
        if self._failing:
            self._failing = False
            logger.info("heartbeat_write_recovered")

    async def report(self, loops: tuple[str, ...] = LOOPS) -> list[LoopHealth]:
        """The state of each loop, in the order given."""
        if self._client is None:
            return [LoopHealth(loop=loop, state="unknown") for loop in loops]
        try:
            values = await self._client.mget([self._key(loop) for loop in loops])
        except (RedisError, OSError):
            return [LoopHealth(loop=loop, state="unknown") for loop in loops]

        report: list[LoopHealth] = []
        for loop, value in zip(loops, values, strict=True):
            if value is None:
                report.append(LoopHealth(loop=loop, state="stale"))
                continue
            last = value.decode("utf-8") if isinstance(value, bytes | bytearray) else str(value)
            report.append(LoopHealth(loop=loop, state="beating", last_beat=last))
        return report


__all__ = ["LOOPS", "Heartbeat", "LoopHealth", "ttl_for"]
