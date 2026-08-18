"""Time access.

Nothing in the system calls ``datetime.now()`` directly. Time arrives through a
:class:`Clock`, so SLA decay, scheduling windows and maintenance intervals are
testable without sleeping. ``ruff``'s DTZ rules additionally ban naive datetimes.

All internal time is UTC. The farm timezone is applied only at presentation and
in open-hours rules.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of the current instant."""

    def now(self) -> datetime:
        """Timezone-aware UTC instant."""
        ...


class SystemClock:
    """Real wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for tests; advances only when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        self._now += delta
        return self._now

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = moment.astimezone(UTC)
