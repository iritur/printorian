"""Ceilings on how often one caller may ask for something expensive.

Two mechanisms, because two different things need protecting and conflating them
gets one of them wrong.

**`RateLimiter`** is a fixed-window counter for endpoints whose *cost* is the
problem. `POST /pricing/quote` takes an optional actor, so it is reachable from
the public internet without signing in, and every call parses a mesh — seconds of
CPU (`core.cpu`). A ceiling here is the difference between a slow endpoint and a
farm whose console stops responding because somebody is posting 20 MB files in a
loop.

**`Lockout`** counts *failures* and then refuses for a while. Sign-in is the case:
`identity.events.SignInFailed` has described itself as "the raw material for
lockout and audit" since it was written, and only the audit half existed. Argon2
makes each guess expensive for the server as well as the attacker, so an
unthrottled sign-in endpoint is both a credential-stuffing target and a way to
burn the API's CPU.

**In-process, and deliberately so.** The deployment is one API process (ADR-0003);
a Redis-backed counter would buy correctness across replicas that do not exist and
add a dependency to the path that has to work when Redis is down. Two consequences
are worth knowing rather than discovering: the counters reset on restart, and if
the API is ever scaled out each replica gets its own allowance. Both are recorded
in `docs/DATABASE-REVIEW.md` §9 alongside the other accepted trade-offs; the fix,
when a second replica is real, is the same Redis this process already talks to for
the event relay.

Windows are fixed rather than sliding: a sliding log costs a timestamp per request
per key, and the burst a fixed window permits at a boundary is twice the rate for
one minute — which matters for a login wall and does not matter for a CPU ceiling
that already has a queue behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from printorian.core.clock import Clock
from printorian.core.errors import RateLimitedError

#: Keys tracked before the oldest windows are swept. Bounds the memory one
#: address-space scan can cost: without it, a caller cycling source addresses
#: turns a defence into a leak.
_MAX_KEYS = 20_000


@dataclass(slots=True)
class _Window:
    started_at: datetime
    count: int


class RateLimiter:
    """Fixed-window request counting, keyed by whatever the caller decides."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._windows: dict[str, _Window] = {}

    def check(self, key: str, *, limit: int, window: timedelta) -> None:
        """Count one request against ``key``, or raise if it is over the ceiling.

        Raises :class:`RateLimitedError` carrying the seconds until the window
        rolls over, which the API turns into a ``Retry-After`` header.
        """
        now = self._clock.now()
        current = self._windows.get(key)

        if current is None or now - current.started_at >= window:
            self._sweep(now, window)
            self._windows[key] = _Window(started_at=now, count=1)
            return

        if current.count >= limit:
            remaining = window - (now - current.started_at)
            raise RateLimitedError(
                "error.rate_limited",
                retry_after_seconds=max(1, int(remaining.total_seconds()) + 1),
                limit=limit,
            )

        current.count += 1

    def _sweep(self, now: datetime, window: timedelta) -> None:
        """Drop windows that have rolled over, when the table has grown large.

        Only on the miss path and only past `_MAX_KEYS`, so the common case — a
        hit on an existing window — stays a dict lookup and an increment.
        """
        if len(self._windows) < _MAX_KEYS:
            return
        self._windows = {
            key: value for key, value in self._windows.items() if now - value.started_at < window
        }


@dataclass(slots=True)
class _Failures:
    count: int
    locked_until: datetime | None


class Lockout:
    """Counts failures against a key and refuses it for a while once it trips."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._failures: dict[str, _Failures] = {}

    def guard(self, key: str) -> None:
        """Refuse if ``key`` is currently locked out. Call *before* the attempt."""
        record = self._failures.get(key)
        if record is None or record.locked_until is None:
            return

        now = self._clock.now()
        if now >= record.locked_until:
            # Served its time. Cleared entirely rather than merely unlocked, so a
            # single further mistake does not re-lock an innocent person who has
            # just waited out somebody else's attack on their address.
            self._failures.pop(key, None)
            return

        raise RateLimitedError(
            "error.identity.locked_out",
            retry_after_seconds=max(1, int((record.locked_until - now).total_seconds()) + 1),
        )

    def record_failure(self, key: str, *, limit: int, penalty: timedelta) -> None:
        """Count one failed attempt, locking ``key`` once it reaches ``limit``."""
        if len(self._failures) >= _MAX_KEYS:
            self._prune()
        record = self._failures.setdefault(key, _Failures(count=0, locked_until=None))
        record.count += 1
        if record.count >= limit:
            record.locked_until = self._clock.now() + penalty
            record.count = 0

    def clear(self, key: str) -> None:
        """Forget the failures for ``key``. Called on a successful sign-in."""
        self._failures.pop(key, None)

    def _prune(self) -> None:
        now = self._clock.now()
        self._failures = {
            key: value
            for key, value in self._failures.items()
            if value.locked_until is not None and value.locked_until > now
        }


__all__ = ["Lockout", "RateLimiter"]
