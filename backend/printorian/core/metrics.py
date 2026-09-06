"""The Prometheus exposition, and the one rule that makes it honest.

INFRASTRUCTURE §5 lists ten domain metrics and argues that request rates and CPU
graphs will not tell you the farm has stopped earning. This module is the
mechanics under them: a registry, the metric families, and the text writer. It
holds no reading of its own — no database, no Redis, no clock — so it is
unit-testable without Postgres and so the *worker* exporter (a later slice) can
use exactly this. That is also why it lives in `core`: `.importlinter`'s layers
contract makes `printorian.api` and `printorian.workers` siblings that may not
import each other, which is the same reason `LIVE_PATTERNS` lives in `core.relay`.

**A metrics exporter's natural default is zero, and zero is the flattering answer
for every series here** (root CLAUDE.md §1, ADR-0007). "Nothing was measured" and
"the measurement was zero" are different readings, and a graph cannot tell them
apart after the fact: an unobserved fleet exported as `printers_offline 0` reads
as a healthy farm for as long as nobody looks at the collection side. So absence
is represented by *absence* — a series with no sample line at all — and
:func:`observe` is that rule as a function rather than as a convention every
caller has to remember.

That is also why nothing here uses `prometheus_client`'s ordinary `Gauge`. A
`Gauge` with no labels initialises itself to `0.0` the moment it is constructed,
so merely *declaring* a series would publish a reading the farm never took. The
`*MetricFamily` types below start with no samples and gain one only when
something is measured, which is the shape this rule needs.

**Nothing here escapes a label value by hand.** A printer named with a quote, a
backslash or a newline in it is the input a hand-rolled writer gets wrong, and
the failure is silent — a scraper parses a different metric, or rejects the whole
scrape. `tests/unit/test_metrics_exposition.py` round-trips such a name through
this package's own parser, which is the test that justifies taking the dependency.

**A registry per scrape, never the process-global default.** Two `create_app`
instances in one test session would otherwise accumulate each other's series, and
a duplicate registration raises rather than merges — a test failure that names
neither of the two tests responsible for it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Final

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from prometheus_client.registry import Collector

#: The exposition's content type, version parameter and all. Re-exported so the
#: route sets it from the library rather than from a string someone typed: the
#: `version=` and `escaping=` parameters are part of the contract a scraper reads,
#: not decoration, and they move when the library does.
CONTENT_TYPE: Final = CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# The names, spelled exactly as INFRASTRUCTURE §5's code block spells them.
#
# Constants rather than literals at each call site, and asserted against the
# document by `tests/unit/test_metrics_exposition.py`. A metric name is a public
# interface — an alert rule and a dashboard panel both hard-code it — so renaming
# one silently is how a farm ends up with a green dashboard that is reading a
# series nobody writes any more. The test is the cheap direction of root
# CLAUDE.md §4: the names and the prose cannot part company without a failure.
# ---------------------------------------------------------------------------

#: ADR-0007. Per printer, and `1` means the worker *observed* a failure to
#: connect — never "we did not hear from it".
PRINTERS_OFFLINE: Final = "printorian_printers_offline"

#: The failure `core.config` names explicitly: the maintenance sweep stopping
#: without saying so, until a month arrives with no partition to land in.
TELEMETRY_PARTITION_MONTHS_AHEAD: Final = "printorian_telemetry_partition_months_ahead"

#: `pg_stat_archiver.failed_count`. ADR-0019's guarantee rests on archived WAL.
WAL_ARCHIVE_FAILURES: Final = "printorian_wal_archive_failures_total"

#: The three of §5's ten that this process can actually measure today. Named as a
#: set so the doc-drift test iterates it rather than repeating the list, and so a
#: fourth series added here cannot skip that check.
SERVED: Final = (
    PRINTERS_OFFLINE,
    TELEMETRY_PARTITION_MONTHS_AHEAD,
    WAL_ARCHIVE_FAILURES,
)

_Family = GaugeMetricFamily | CounterMetricFamily


class Series:
    """One metric family, filled in for a single scrape and then thrown away.

    Carries its declared label names alongside the family because `observe` takes
    labels by keyword and the library only keeps them positionally. Holding them
    here rather than reading the family's private attribute keeps this module
    working when the library rearranges its internals — and gives a wrong label
    name a loud error instead of a sample silently attached to the wrong axis.
    """

    def __init__(self, family: _Family, *, name: str, labels: tuple[str, ...]) -> None:
        self.family = family
        #: The name as INFRASTRUCTURE §5 spells it. Not `family.name`, which for a
        #: counter has had its `_total` suffix stripped — the exposition puts it
        #: back, so the two disagree and only this one is the published name.
        self.name = name
        self.labels = labels


def gauge(name: str, documentation: str, *labels: str) -> Series:
    """A gauge with no samples yet: a reading that has not been taken."""
    return Series(
        GaugeMetricFamily(name, documentation, labels=list(labels)),
        name=name,
        labels=labels,
    )


def counter(name: str, documentation: str, *labels: str) -> Series:
    """A counter with no samples yet.

    Name it with the `_total` suffix the exposition publishes; the library strips
    it internally and adds it back on the way out.
    """
    return Series(
        CounterMetricFamily(name, documentation, labels=list(labels)),
        name=name,
        labels=labels,
    )


def observe(series: Series, value: float | None, **labels: str) -> None:
    """Record one measurement — or, for ``None``, record nothing at all.

    ``None`` means *the farm did not measure this*, and the honest exposition of
    that is an absent series: no sample line, so a scraper stores no point and a
    graph shows a gap rather than a confident zero. Root CLAUDE.md §1 is the whole
    of the argument, and it is a function rather than a convention because
    ``value or 0`` is one keystroke away at every call site and reads as
    defensive rather than as the invention it is.

    A measured ``0`` is the opposite case and *is* recorded — two months of
    partition headroom and zero months of it are both real readings, and a helper
    that emitted nothing ever would satisfy the paragraph above while telling you
    nothing. Both halves are pinned by `tests/unit/test_metrics_exposition.py`;
    the second is what makes the first falsifiable.

    Label values are passed through the library's writer, never formatted here.
    """
    if value is None:
        return
    if tuple(labels) != series.labels:
        # A programming error, and loud: the alternative is a sample attached to
        # the wrong axis, which parses cleanly and misroutes every alert built on
        # it. Order matters as well as membership, because the family stores
        # label values positionally.
        raise ValueError(f"{series.name} declares labels {series.labels}, got {tuple(labels)}")
    series.family.add_metric([labels[label] for label in series.labels], value)


class _Snapshot(Collector):
    """The families of one scrape, handed to the registry as a single collector.

    A collector rather than individually registered metrics because the families
    are already fully measured by the time the registry sees them: the readings
    are awaited in the route (nothing async may run inside `collect`), and this
    only has to hand them over.
    """

    def __init__(self, families: Sequence[Metric]) -> None:
        self._families = families

    def collect(self) -> Iterator[Metric]:
        yield from self._families


def build_registry(*series: Series) -> CollectorRegistry:
    """A fresh registry holding exactly these families.

    Fresh every scrape, and never `prometheus_client`'s process-global default:
    the default is shared by everything in the process, so a second `create_app`
    in the same test session would collide with the first's registrations and
    fail in a way that names neither test.
    """
    registry = CollectorRegistry()
    registry.register(_Snapshot([entry.family for entry in series]))
    return registry


def render(registry: CollectorRegistry) -> tuple[bytes, str]:
    """The exposition body and the content type to serve it under."""
    return generate_latest(registry), CONTENT_TYPE


def names_in(registries: Iterable[CollectorRegistry]) -> set[str]:
    """Every sample name present in the given registries.

    Reads the registry rather than the rendered body, which is what
    `tests/unit/test_metrics_exposition.py` needs in order to say that two scrapes
    share nothing: "the second registry carries no samples" is a claim about
    structure, and a search of a text body could only report that some string is
    absent from it.

    The money guard lives in `tests/api/test_metrics_api.py` and cannot call this
    — it holds an HTTP response and no registry — so it parses the body with the
    library's own parser instead. Same rule, read at the only place each caller can
    reach it, and neither of them by grepping.
    """
    return {
        sample.name
        for registry in registries
        for family in registry.collect()
        for sample in family.samples
    }


__all__ = [
    "CONTENT_TYPE",
    "PRINTERS_OFFLINE",
    "SERVED",
    "TELEMETRY_PARTITION_MONTHS_AHEAD",
    "WAL_ARCHIVE_FAILURES",
    "Series",
    "build_registry",
    "counter",
    "gauge",
    "names_in",
    "observe",
    "render",
]
