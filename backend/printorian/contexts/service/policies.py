"""What a failure is, and the two figures the farm may honestly derive from one.

Two rules live here, and both are ADR-0007 wearing different clothes.

**A driver code is not a cause.** `drivers/bambu/report.py` produces
``bambu.print_error.{code}``; the farm holds no table turning a vendor code into
«слом филамента», so a failure the driver opened carries the code verbatim and a
``NULL`` cause until a person names one. Guessing would put a measurement in the
«Причины отказов» funnel that nobody made.

**A rate with no denominator is not zero.** `failures_per_1000_hours` answers
``None`` for a machine the summariser never covered, because the alternative reads
as "perfectly reliable" — and the worse the coverage the healthier the farm looks,
silently. The same distinction runs the other way: a machine that *was* measured
and did not break answers a real ``Decimal(0)``, which is a different fact.

Pure: `Decimal` only, no floats, no clock, no session. The aggregation these two
are fed from is SQL in `reliability.py`; what is here is the arithmetic that has to
be readable on its own, because it is the arithmetic somebody will argue with.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

#: Seconds in the thousand machine-hours the failure rate is quoted per.
#: «Отказов/год» on the kit's reliability table is the same figure on a different
#: ruler, and a per-hour rate reads as 0.00 for every machine on the farm.
SECONDS_PER_1000_HOURS = Decimal(1000 * 3600)

_RATE_PLACES = Decimal("0.01")
_MINUTE_PLACES = Decimal("0.1")


class FailureOrigin(StrEnum):
    """Who noticed. The kit draws «СООБЩИЛ ДРАЙВЕР» as a badge on the ticket.

    Kept as a recorded fact rather than inferred from whether `recorded_by` is set:
    a person recording a failure on behalf of a machine that had already been
    switched off would otherwise be indistinguishable from the sweep, and the two
    have different evidence behind them.
    """

    #: Opened by `workers/service.py` from a state the machine itself reported.
    DRIVER = "driver"
    #: Opened by somebody at the machine.
    PERSON = "person"


class FailureCause(StrEnum):
    """The «Причины отказов» funnel, as a closed set.

    Taken from the kit's five bars plus `OTHER`. A closed set rather than free
    text for the reason `OperationKind` is one: a histogram over prose is a
    histogram over spelling, and a cause nobody named cannot be counted.

    There is deliberately no ``UNKNOWN`` member. "Nobody has named it" is the
    column being ``NULL``, and `reliability.py` reports that as `uncategorised`
    beside the histogram rather than inside it — `OTHER` means *named*, and it was
    something else.
    """

    FILAMENT_BREAK = "filament_break"
    NOZZLE_CLOG = "nozzle_clog"
    ADHESION = "adhesion"
    NETWORK = "network"
    AMS_SENSOR = "ams_sensor"
    OTHER = "other"


def failures_per_1000_hours(failures: int, observed_seconds: Decimal | None) -> Decimal | None:
    """Failures over the hours the farm actually watched this machine.

    ``None`` when `observed_seconds` is absent **or zero**. Absent means no
    `metric_rollups` row covered the machine in this window; zero means every row
    that did cover it accounted for no time. Neither is a denominator, and both
    would render as a perfect score — the flattering error CLAUDE.md §1 names.

    A real ``Decimal(0)`` when there were observed hours and no failures, because
    "measured, and it never broke" and "nobody measured it" are two different
    facts and must not arrive at a client as the same number.
    """
    if not observed_seconds:
        return None
    rate = Decimal(failures) * SECONDS_PER_1000_HOURS / observed_seconds
    return rate.quantize(_RATE_PLACES)


def mttr_minutes(repair_seconds: Decimal | None, closed_failures: int) -> Decimal | None:
    """Mean time to repair, over the failures that were actually repaired.

    ``None`` when nothing has been closed. A failure still open contributes to
    neither side of the fraction: it has no repair duration yet, and substituting
    ``now - detected_at`` would make MTTR climb while nobody is working — a number
    the farm never measured, moving on its own (ADR-0007).

    `repair_seconds` is the **sum** over closed rows rather than an average
    computed in SQL, so the "no closed repairs, no figure" rule sits here beside
    the rule it mirrors in `failures_per_1000_hours` instead of being an
    ``AVG`` returning ``NULL`` that nobody can point at.
    """
    if closed_failures <= 0 or repair_seconds is None:
        return None
    return (repair_seconds / Decimal(closed_failures) / Decimal(60)).quantize(_MINUTE_PLACES)


__all__ = [
    "SECONDS_PER_1000_HOURS",
    "FailureCause",
    "FailureOrigin",
    "failures_per_1000_hours",
    "mttr_minutes",
]
