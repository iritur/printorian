"""The window rule: alignment, the clamp, and the two ceilings.

Pure — no database, no fixtures. One rule serves both metric routes, so a client
that learned the semantics once does not have to learn them twice, and these are
the cases that rule has to get right for that promise to be worth anything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from printorian.contexts.fleet.measures import (
    MAX_BUCKETS,
    MAX_WINDOW_HOURS,
    Grain,
    resolve_window,
)
from printorian.core.errors import ValidationError

#: A Tuesday afternoon, twenty past the hour, so alignment has something to bite.
NOW = datetime(2026, 3, 3, 14, 20, 41, tzinfo=UTC)
OPEN_HOUR = datetime(2026, 3, 3, 14, 0, tzinfo=UTC)


def resolved(
    since: datetime, until: datetime | None = None, grain: Grain = Grain.HOUR, now: datetime = NOW
):
    return resolve_window(since=since, until=until, grain=grain, now=now)


def test_both_ends_are_truncated_down_to_the_hour() -> None:
    """Down, at both ends — the buckets are cut on the hour and nothing else exists.

    Rounding `until` *up* would ask for a bucket that has not been written; rounding
    `since` up would drop the hour the caller asked to start in.
    """
    window = resolved(
        datetime(2026, 3, 3, 9, 59, 59, tzinfo=UTC), datetime(2026, 3, 3, 12, 0, 1, tzinfo=UTC)
    )

    assert window.since == datetime(2026, 3, 3, 9, 0, tzinfo=UTC)
    assert window.until == datetime(2026, 3, 3, 12, 0, tzinfo=UTC)


def test_until_defaults_to_the_start_of_the_current_hour() -> None:
    """The sweep never writes the open hour.

    Serving it from raw samples would make the newest heat cell computed by a
    different rule than the other 167 — a discontinuity at the exact cell people
    look at first.
    """
    window = resolved(NOW - timedelta(hours=5))

    assert window.until == OPEN_HOUR


def test_an_until_in_the_future_is_clamped_down_and_echoed() -> None:
    """Clamped rather than refused, and the *clamped* value comes back.

    A client asking for "up to midnight" is asking a reasonable question; answering
    it with hours nobody has summarised is not. Echoing the clamp is what lets the
    caller see which window it actually got.
    """
    window = resolved(NOW - timedelta(days=2), NOW + timedelta(days=1))

    assert window.until == OPEN_HOUR


def test_an_empty_window_is_refused() -> None:
    """Including the case the clamp creates — asking only for the open hour."""
    with pytest.raises(ValidationError) as refused:
        resolved(OPEN_HOUR, NOW)

    assert refused.value.code == "error.fleet.metrics.window_empty"


def test_a_grid_wider_than_thirty_one_days_is_refused_rather_than_truncated() -> None:
    """Deliberately unlike `THROUGHPUT_LIMIT`, which floors a scalar and flags it.

    That works for a number. A heat map exists to show a *shape*, and a silently
    missing left edge is a lie about the shape — so the request is refused and the
    ceiling is named, rather than the answer being quietly shortened.
    """
    with pytest.raises(ValidationError) as refused:
        resolved(NOW - timedelta(days=32))

    assert refused.value.code == "error.fleet.metrics.window_too_wide"
    assert refused.value.details == {"max_hours": "744", "requested_hours": "768"}


def test_thirty_one_days_of_buckets_is_exactly_allowed() -> None:
    window = resolved(OPEN_HOUR - timedelta(hours=MAX_BUCKETS))

    assert (window.until - window.since) / timedelta(hours=1) == MAX_BUCKETS


def test_a_total_may_span_a_year_where_a_grid_may_not() -> None:
    """The wider ceiling is a response-size policy, not a data one.

    `metric_rollups` has no retention and is meant to be kept for ever, so this
    number is stated rather than inherited from `telemetry_retention_days` — the
    table will outlive the samples.
    """
    since = OPEN_HOUR - timedelta(days=200)

    assert resolved(since, grain=Grain.TOTAL).since == since
    with pytest.raises(ValidationError):
        resolved(since, grain=Grain.HOUR)


def test_even_a_total_stops_at_three_hundred_and_sixty_six_days() -> None:
    with pytest.raises(ValidationError) as refused:
        resolved(OPEN_HOUR - timedelta(hours=MAX_WINDOW_HOURS + 1), grain=Grain.TOTAL)

    assert refused.value.details["max_hours"] == str(MAX_WINDOW_HOURS)


def test_a_timestamp_with_no_zone_is_refused_rather_than_assumed_utc() -> None:
    """A naive datetime is an instant nobody stated.

    Assuming UTC would shift a Moscow client's window by three hours and return a
    perfectly plausible grid for the wrong day, which is the worst kind of wrong
    answer: one nothing downstream can detect.
    """
    with pytest.raises(ValidationError) as refused:
        resolved(datetime(2026, 3, 1, 9, 0))  # noqa: DTZ001 - the whole point

    assert refused.value.code == "error.fleet.metrics.naive_timestamp"
    assert refused.value.details == {"field": "since"}


def test_a_zoned_timestamp_that_is_not_utc_is_converted_and_not_refused() -> None:
    """Moscow's 12:40 is 09:00 UTC once truncated. The client may speak local time;
    the ruler is UTC, as everything else stored is."""
    moscow = datetime(2026, 3, 3, 12, 40, tzinfo=timezone(timedelta(hours=3)))

    assert resolved(moscow).since == datetime(2026, 3, 3, 9, 0, tzinfo=UTC)
