"""Service — what broke, when it was working again, and how often that happens.

Public interface.

**Why this is a context of its own rather than four more files in `fleet`.** The
next person to fold it back in will reason that a failure is obviously a fact about
a printer, and they will be right about the fact and wrong about the boundary.
`fleet` is the *registry plus measured history*: the row a poll overwrites, and the
hours `metric_rollups` summarised. A failure is neither — it is opened by a person
or a sweep, closed by an observation, named by somebody's judgement, and the farm
is judged on it. That is work-management, which is why `postproduction` and
`packaging` are their own contexts rather than tables on `ordering`. The practical
argument runs the same way: `fleet` is already 2 656 lines over fourteen files with
`service.py` at 398 of the 400-line gate, so there is no room to put this there
without splitting something else first.

Two rules define this context, and both are ADR-0007:

* **a driver code is not a cause.** `drivers/bambu/report.py` yields
  ``bambu.print_error.{code}``; nothing on this farm maps a vendor code to «слом
  филамента», so a failure the sweep opened carries the code and a ``NULL`` cause
  until a person names one;
* **nothing here divides.** The failure counts are numerators; the denominator is
  `metric_rollups.observed_seconds`, which belongs to `fleet`. The two meet exactly
  once, in `api/routers/_service_reliability.py`, because a context that reached
  across for its own denominator is a context that will eventually reach for the
  roster instead — the flattering error CLAUDE.md §1 names.

Seconds and counts only. No rubles, no kilowatt-hours: `VIEW_FINANCIALS` is kept
apart from every production permission, and the kit's «ПОТЕРЯ 3 820 ₽» is a
composition for a different route behind a different gate.
"""

from printorian.contexts.service.policies import (
    SECONDS_PER_1000_HOURS,
    FailureCause,
    FailureOrigin,
    failures_per_1000_hours,
    mttr_minutes,
)
from printorian.contexts.service.reliability import (
    CauseCount,
    FailureSummary,
    FailureTally,
    failure_summary,
)
from printorian.contexts.service.schemas import (
    FailureView,
    NameCause,
    RecordFailure,
    RestoreFailure,
)
from printorian.contexts.service.service import ServiceDesk

__all__ = [
    "SECONDS_PER_1000_HOURS",
    "CauseCount",
    "FailureCause",
    "FailureOrigin",
    "FailureSummary",
    "FailureTally",
    "FailureView",
    "NameCause",
    "RecordFailure",
    "RestoreFailure",
    "ServiceDesk",
    "failure_summary",
    "failures_per_1000_hours",
    "mttr_minutes",
]
