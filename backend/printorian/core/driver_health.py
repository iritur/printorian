"""Which printers the farm is actually connected to, and since when.

The pool of live printer connections belongs to the *worker* process
(`workers/drivers.py`), kept alive between passes so a fifty-machine farm does not
reconnect every tick. ARCHITECTURE §10 drew the right conclusion from that and
then stopped one step short: the API cannot see a connection, so readiness must
not claim to — but "which process owns the fact" is not an argument about whether
the fact should be observable. A driver unreachable for six hours is still a
printer the farm believes it can dispatch to, and until this existed nothing
outside a screen someone happened to be looking at said otherwise.

So the worker publishes what it already knows down the channel it already uses.
This is `core.heartbeat` applied to a second fact, and deliberately shaped the
same way: Redis with an expiry, because a reading about *now* is the one kind of
state that should evaporate on its own, and a store that cannot be read reports
`unknown` rather than inventing an answer (ADR-0007).

**Two keys, with two windows, and the difference is the whole design.** The state
keys carry the readings and expire with the loop that writes them. The roster —
the printers the worker was asked to drive on its last pass — is written last and
expires later. Without that gap, a worker that stops publishing takes the roster
with it, and the report goes quietly empty: the farm would look like it has no
printers rather than like it has printers nobody can vouch for. With it, the
readings lapse first and every printer the roster still names reports `unknown`,
which is the honest answer and the one an alert can act on.

Nothing here is a source of truth. The roster is what the *worker observed*, never
what the `printers` table lists — reading the database for it would report on
machines the worker may never have tried to reach, which is the denominator
mistake root CLAUDE.md §1 is about.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

#: A live connection, as of the last pass that looked.
CONNECTED: Final = "connected"
#: The worker tried and could not connect. `code` says why (ADR-0012).
UNAVAILABLE: Final = "unavailable"
#: The worker named this printer and then stopped publishing readings for it —
#: or the store could not be read at all. Not `ok`, and not a failure either.
UNKNOWN: Final = "unknown"

#: How much longer the roster lives than the readings it names. Four passes of
#: slack: long enough that a single slow sweep does not erase the roster, short
#: enough that a farm switched off for an afternoon stops reporting printers it
#: no longer has.
_ROSTER_TTL_MULTIPLIER: Final = 4


@dataclass(frozen=True, slots=True)
class DriverHealth:
    """What is known about one printer's connection right now."""

    printer_id: str
    #: The printer's name, carried so a report can be read without a second
    #: lookup — an alert that can only say `01a0…78cc` is unreachable is an alert
    #: somebody has to go and decode.
    name: str
    #: `CONNECTED`, `UNAVAILABLE` or `UNKNOWN`.
    state: str
    #: The error code behind `UNAVAILABLE`. A code, never prose (ADR-0012).
    code: str | None = None
    #: ISO-8601, when the current state began. What turns "unreachable" into
    #: "unreachable since 03:14", which is the difference between a printer
    #: somebody switched off a minute ago and one nobody has noticed all day.
    since: str | None = None


class DriverStates:
    """Publishes and reads per-printer connection state, across processes."""

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

    def _roster_key(self) -> str:
        return f"{self._prefix}:drivers"

    def _state_key(self, printer_id: str) -> str:
        return f"{self._prefix}:driver:{printer_id}"

    async def publish(self, states: Sequence[DriverHealth], *, ttl_seconds: int) -> None:
        """Record this pass's readings. Never raises into the sweep.

        The readings go first and the roster last, so a reader that catches the
        write half-done sees a roster naming printers whose readings are already
        there — never the reverse, which would report `unknown` for a printer the
        worker had just successfully connected to.
        """
        if self._client is None:
            return
        try:
            pipeline = self._client.pipeline()
            for health in states:
                pipeline.set(
                    self._state_key(health.printer_id),
                    json.dumps({"state": health.state, "code": health.code, "since": health.since}),
                    ex=ttl_seconds,
                )
            pipeline.set(
                self._roster_key(),
                json.dumps([{"id": health.printer_id, "name": health.name} for health in states]),
                ex=ttl_seconds * _ROSTER_TTL_MULTIPLIER,
            )
            await pipeline.execute()
        except (RedisError, OSError) as failure:
            if not self._failing:
                self._failing = True
                logger.warning("driver_states_write_failed", error=str(failure))
            return
        if self._failing:
            self._failing = False
            logger.info("driver_states_write_recovered")

    async def report(self) -> list[DriverHealth]:
        """Every printer the worker last said it was driving, and its state.

        An empty list means *nothing has been published* — no Redis, or a worker
        that has been down longer than the roster's window. It does not mean the
        farm has no printers, and the endpoint that renders it says so.
        """
        roster = await self._roster()
        if not roster:
            return []

        try:
            values = await self._client.mget(  # type: ignore[union-attr]
                [self._state_key(printer_id) for printer_id, _name in roster]
            )
        except (RedisError, OSError):
            return [
                DriverHealth(printer_id=printer_id, name=name, state=UNKNOWN)
                for printer_id, name in roster
            ]

        report: list[DriverHealth] = []
        for (printer_id, name), value in zip(roster, values, strict=True):
            reading = _payload(value)
            if not isinstance(reading, dict):
                # Named by the roster, no readable reading within its window. The
                # case the two windows exist to produce.
                report.append(DriverHealth(printer_id=printer_id, name=name, state=UNKNOWN))
                continue
            report.append(
                DriverHealth(
                    printer_id=printer_id,
                    name=name,
                    state=str(reading.get("state", UNKNOWN)),
                    code=_text(reading.get("code")),
                    since=_text(reading.get("since")),
                )
            )
        return report

    async def _roster(self) -> list[tuple[str, str]]:
        """The printers the worker last said it was driving, in the order given."""
        if self._client is None:
            return []
        try:
            raw = await self._client.get(self._roster_key())
        except (RedisError, OSError):
            return []
        entries = _payload(raw)
        if not isinstance(entries, list):
            return []
        return [
            (str(entry["id"]), str(entry["name"]))
            for entry in entries
            if isinstance(entry, dict) and "id" in entry and "name" in entry
        ]


def _payload(value: object) -> object:
    """Whatever was stored, or ``None`` if it is missing or no longer readable.

    A key written by an older version, or truncated, is treated as absent rather
    than raising into a health endpoint — the report is the thing that has to keep
    working when something else has stopped.
    """
    if value is None:
        return None
    text = value.decode("utf-8") if isinstance(value, bytes | bytearray) else str(value)
    try:
        return json.loads(text)
    except ValueError:
        return None


def _text(value: object) -> str | None:
    """A stored optional string, or ``None`` for anything that is not one."""
    return value if isinstance(value, str) else None


__all__ = ["CONNECTED", "UNAVAILABLE", "UNKNOWN", "DriverHealth", "DriverStates"]
