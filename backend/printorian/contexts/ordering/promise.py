"""Turning a print time into a date the farm will stand behind.

Pure, and deliberately separate from the pricing engine: what a job *costs* and
when it will *be ready* are different questions, and ADR-0002 keeps the engine
free of anything that is not money.

The policy arrives as an argument (:class:`PromisePolicy`), never looked up here —
the same rule the pricing engine follows for `RateSnapshot`. `contexts.settings`
resolves the farm's overrides into a `PromisePolicy` at the read edge, so the
kit's three parameters (`promise_buffer_percent`, `min_lead_hours`,
`rush_lead_hours`) change what the *next* quote promises and nothing already
agreed — the promise is computed from the snapshot, not from a module constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class PromisePolicy:
    """The promise parameters, as data.

    Frozen for the same reason `RateSnapshot` is: a promise pinned to an order
    cannot be edited afterwards, and a policy handed to `promised_hours` cannot
    be mutated out from under the calculation.
    """

    #: Headroom over the raw print time. The estimator answers "how long does the
    #: machine run"; a promise has to survive the queue, post-processing and a
    #: failed plate.
    promise_buffer_percent: Decimal = Decimal(40)
    #: Nothing is promised sooner than this, however small the part.
    min_lead_hours: Decimal = Decimal(24)
    #: What the rush surcharge buys.
    rush_lead_hours: Decimal = Decimal(18)


#: The defaults, kept as bare names for the places that still want a number
#: rather than a policy object. `resolve_promise` overrides them, never these.
PROMISE_BUFFER_PERCENT = PromisePolicy().promise_buffer_percent
MIN_LEAD_HOURS = PromisePolicy().min_lead_hours
RUSH_LEAD_HOURS = PromisePolicy().rush_lead_hours


def promised_hours(
    *,
    policy: PromisePolicy | None = None,
    print_minutes: Decimal,
    quantity: int = 1,
    rush: bool = False,
) -> Decimal:
    """Hours from now that this job can be promised for.

    Scales with quantity because the machine time does: ten of a part is ten
    prints, whether they share a plate or not. The buffer is then applied to the
    whole, not per unit — a batch does not need ten separate contingencies.
    """
    resolved = policy or PromisePolicy()

    if rush:
        return resolved.rush_lead_hours

    machine_hours = (print_minutes * Decimal(max(quantity, 1))) / Decimal(60)
    with_buffer = machine_hours * (Decimal(1) + resolved.promise_buffer_percent / Decimal(100))
    return max(with_buffer, resolved.min_lead_hours).quantize(Decimal("1"))
