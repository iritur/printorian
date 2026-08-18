"""Scheduling policy: what "the best printer" means, as data.

Every weight is injected (ADR-0010). A farm that wants to protect due dates above
all else, or one that wants to minimise filament swaps, changes configuration —
not this module.

The rejection codes are re-exported from ``fleet`` rather than restated. There is
exactly one definition of "can this machine take this job" and it lives with the
machines (ARCHITECTURE §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: Reasons that describe the machine as it is *configured*. A job rejected for one
#: of these will never run on that printer, however long anyone waits — so a wait
#: list entry blocked only by these is not waiting for capacity, it is waiting for
#: a person to make a decision.
STRUCTURAL_REJECTIONS: frozenset[str] = frozenset(
    {
        "reject.build_volume",
        "reject.nozzle",
        "reject.no_multi_material",
    }
)

#: Reasons that clear on their own as the farm works: the machine finishes what it
#: is doing, comes back online, or has its service completed.
TRANSIENT_REJECTIONS: frozenset[str] = frozenset(
    {
        "reject.busy",
        "reject.in_maintenance",
        "reject.no_storage",
    }
)

#: Reasons that clear only when somebody mounts filament. Predicting a start time
#: for these would be inventing one — nothing in the system knows when a human will
#: walk over with a spool.
MATERIAL_REJECTIONS: frozenset[str] = frozenset(
    {
        "reject.material_not_loaded",
        "reject.insufficient_material",
        "reject.colour_not_loaded",
    }
)

#: Why a job is on the wait list rather than on a printer.
WAIT_NO_CAPABLE_PRINTER = "waitlist.no_capable_printer"
WAIT_AWAITING_CAPACITY = "waitlist.awaiting_capacity"
WAIT_MATERIAL_NOT_LOADED = "waitlist.material_not_loaded"

# -- score components -------------------------------------------------------
#
# ARCHITECTURE §6 lists "changeover cost" and "batching affinity" as the first two
# soft terms. Implementing them showed both are unreachable: eligibility already
# requires the material *and* every requested colour to be mounted, so any printer
# the scorer sees has them, and both terms are identically zero for every
# candidate. Weighting a constant cannot change an outcome.
#
# What does vary between eligible machines is which machine it is cheapest to give
# up — so the two are replaced by terms that discriminate. Changeover returns as a
# real cost once jobs can be planned onto machines that are still busy, which is
# the queue-depth work this phase does not yet do.
SCORE_CAPABILITY_WASTE = "score.capability_waste"
SCORE_MATERIAL_HEADROOM = "score.material_headroom"
SCORE_AMORTIZATION = "score.amortization"
SCORE_LOAD_BALANCE = "score.load_balance"


@dataclass(frozen=True, slots=True, kw_only=True)
class SchedulingPolicy:
    """How to choose between printers that can all do the job.

    Costs, not merits: every component is a penalty in ``[0, 1]`` scaled by its
    weight, and the cheapest printer wins. One direction throughout means a new
    component cannot be added with its sign accidentally inverted — which is the
    kind of bug that produces a plausible-looking schedule that is quietly wrong.
    """

    #: A job due within this many hours is urgent and is placed first.
    due_soon_hours: Decimal = Decimal(24)

    #: Keep flexible machines free: a four-colour AMS spent on a single-colour job
    #: is capacity the farm cannot use for the job that needs four.
    weight_capability_waste: Decimal = Decimal(3)
    #: Prefer a spool with room to spare. Running out mid-print wastes the whole
    #: plate and the hours already spent on it.
    weight_material_headroom: Decimal = Decimal(4)
    #: Prefer cheaper machines for ordinary work, keeping expensive ones free.
    weight_amortization: Decimal = Decimal(1)
    #: Spread work rather than queueing everything on one printer.
    weight_load_balance: Decimal = Decimal(2)

    #: Queue depth, in minutes, treated as "fully loaded" when normalising.
    load_horizon_minutes: Decimal = Decimal(480)
    #: Amortization, per hour, treated as "expensive" when normalising.
    expensive_per_hour: Decimal = Decimal(50)
    #: Remaining filament, as a multiple of what the job needs, at which headroom
    #: stops being a worry.
    comfortable_headroom: Decimal = Decimal(3)
