"""The exposition mechanics, and the one rule that makes them honest.

No database and no application: `core.metrics` holds no reading of its own, which
is what lets these cases be about the *shape* of an exposition rather than about
the farm. What they protect is the ADR-0007 property in both directions —
`None` publishes nothing, a measured `0` publishes a zero — because a helper that
emitted nothing ever would satisfy the first half and tell you nothing.

The escaping case is the one that justifies taking `prometheus-client` at all, and
it is written as a round-trip through the library's own parser rather than as a
string comparison: what matters is that a scraper reads back the label value that
was measured, not that the writer produced any particular byte sequence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from prometheus_client.parser import text_string_to_metric_families

from printorian.core import metrics

REPO = Path(__file__).resolve().parents[3]
INFRASTRUCTURE = REPO / "docs" / "INFRASTRUCTURE.md"


def _samples(*series: metrics.Series) -> list[tuple[str, dict[str, str], float]]:
    """Everything a scraper would read back, parsed rather than pattern-matched."""
    body, _ = metrics.render(metrics.build_registry(*series))
    return [
        (sample.name, dict(sample.labels), sample.value)
        for family in text_string_to_metric_families(body.decode("utf-8"))
        for sample in family.samples
    ]


def test_a_reading_nobody_took_publishes_no_sample() -> None:
    """`None` is "not measured", and its honest exposition is an absent series.

    Not a zero: a scraper stores no point, so the graph shows a gap and an alert
    keyed on the value stays quiet. The farm being unobserved must never look like
    the farm being fine (root CLAUDE.md §1).
    """
    offline = metrics.gauge(metrics.PRINTERS_OFFLINE, "doc", "printer", "brand")

    metrics.observe(offline, None, printer="p1", brand="bambu")

    assert _samples(offline) == []


def test_a_measured_zero_publishes_a_zero() -> None:
    """The converse, and the case that makes the one above falsifiable.

    Zero months of partition headroom is a real reading — the one that should page
    somebody — so it has to arrive as `0`. Without this case, a helper that
    silently published nothing at all would pass the test above.
    """
    headroom = metrics.gauge(metrics.TELEMETRY_PARTITION_MONTHS_AHEAD, "doc")

    metrics.observe(headroom, 0)

    assert _samples(headroom) == [(metrics.TELEMETRY_PARTITION_MONTHS_AHEAD, {}, 0.0)]


def test_a_hostile_label_value_survives_the_round_trip() -> None:
    """A printer named with a quote, a backslash and a newline in it.

    This is the whole argument for the dependency. A hand-rolled writer that
    interpolated the name would produce an exposition a scraper either parses as a
    different metric or rejects outright, and the failure is silent at the point
    it is made. Asserted by parsing the body back rather than by matching the
    escaped text, because the escaping rules are the library's business and the
    contract is only that the value survives.
    """
    name = 'P1S "one"\\two\nthree'
    offline = metrics.gauge(metrics.PRINTERS_OFFLINE, "doc", "printer", "brand")

    metrics.observe(offline, 1, printer=name, brand="bambu")

    assert _samples(offline) == [
        (metrics.PRINTERS_OFFLINE, {"printer": name, "brand": "bambu"}, 1.0)
    ]


def test_a_counter_publishes_the_name_the_document_spells() -> None:
    """`_total` survives the library stripping it internally.

    `CounterMetricFamily` takes the suffix off its own `name` and the writer puts
    it back, so the two disagree and only the published one is the name an alert
    rule can be written against.
    """
    failures = metrics.counter(metrics.WAL_ARCHIVE_FAILURES, "doc")

    metrics.observe(failures, 3)

    assert _samples(failures) == [(metrics.WAL_ARCHIVE_FAILURES, {}, 3.0)]


def test_a_label_the_series_never_declared_is_refused() -> None:
    """Loud, because the alternative parses cleanly and misroutes every alert.

    A sample attached to the wrong axis is a valid exposition saying something
    false, and nothing downstream can tell. The family stores label values
    positionally, so order is part of the contract as well as membership.
    """
    offline = metrics.gauge(metrics.PRINTERS_OFFLINE, "doc", "printer", "brand")

    with pytest.raises(ValueError, match=metrics.PRINTERS_OFFLINE):
        metrics.observe(offline, 1, printer="p1", make="bambu")


def test_each_scrape_gets_a_registry_of_its_own() -> None:
    """Two registries share nothing, so no series leaks between two scrapes.

    The failure this prevents is not a wrong number but an unreadable test run:
    `prometheus_client`'s process-global default registry raises on a duplicate
    registration, so two `create_app` instances in one session would collide and
    fail in a way that names neither of the tests responsible.
    """
    first = metrics.gauge(metrics.TELEMETRY_PARTITION_MONTHS_AHEAD, "doc")
    metrics.observe(first, 2)
    second = metrics.gauge(metrics.TELEMETRY_PARTITION_MONTHS_AHEAD, "doc")

    one = metrics.build_registry(first)
    other = metrics.build_registry(second)

    assert one is not other
    assert metrics.names_in([one]) == {metrics.TELEMETRY_PARTITION_MONTHS_AHEAD}
    # The second registry holds a family that was never observed, so it publishes
    # no sample — and crucially it did not inherit the first one's.
    assert metrics.names_in([other]) == set()


def _documented_metric_names() -> set[str]:
    """The names in INFRASTRUCTURE §5's code block, read from the document."""
    text = INFRASTRUCTURE.read_text(encoding="utf-8")
    start = text.index("### The metrics that matter here are domain metrics")
    block = text[start:].split("```")[1]
    return set(re.findall(r"^\s*(printorian_\w+)", block, flags=re.MULTILINE))


def test_every_served_metric_name_is_spelled_the_same_in_the_document() -> None:
    """The cheap direction of the docs-go-stale trap (root CLAUDE.md §4).

    A metric name is a public interface: an alert rule and a dashboard panel both
    hard-code it, so renaming one silently is how a farm ends up with a green
    dashboard reading a series nobody writes any more. Iterating `SERVED` rather
    than repeating the list here means a fourth series cannot skip this check.
    """
    documented = _documented_metric_names()

    assert set(metrics.SERVED) <= documented


def test_the_document_still_lists_more_than_this_slice_serves() -> None:
    """Three series are three series, not the whole of §5.

    If this ever fails, either the remaining collectors landed — in which case
    `SERVED` should say so — or somebody trimmed the document down to what the
    code happens to do, which is the direction that turns a plan into a
    description of the present.
    """
    assert len(_documented_metric_names()) > len(metrics.SERVED)
