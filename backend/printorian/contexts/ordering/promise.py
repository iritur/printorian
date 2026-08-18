"""Turning a print time into a date the farm will stand behind.

Pure, and deliberately separate from the pricing engine: what a job *costs* and
when it will *be ready* are different questions, and ADR-0002 keeps the engine
free of anything that is not money.

**These constants belong in the settings store.** `settings.html` lists all three
by name — `promise_buffer_percent`, `min_lead_hours`, `rush_lead_hours` — as
editable farm parameters, and the store that will hold them is DESIGN-KIT-
INTEGRATION.md §3.4, still unbuilt. They live here as named policy in the
meantime, with the kit's own defaults, rather than as numbers inlined at a call
site where nobody would find them to move later.
"""

from __future__ import annotations

from decimal import Decimal

#: Headroom over the raw print time.
#:
#: The estimator answers "how long does the machine run"; a promise has to survive
#: the queue, post-processing and a failed plate. Quoting the bare print time is
#: how a farm ends up late on work that went exactly as planned.
PROMISE_BUFFER_PERCENT = Decimal(40)

#: Nothing is promised sooner than this, however small the part.
#:
#: A twenty-minute print still has to be scheduled, started, taken off the bed and
#: packed, and the person doing it is not standing over the machine waiting.
MIN_LEAD_HOURS = Decimal(24)

#: What the rush surcharge buys.
RUSH_LEAD_HOURS = Decimal(18)


def promised_hours(*, print_minutes: Decimal, quantity: int = 1, rush: bool = False) -> Decimal:
    """Hours from now that this job can be promised for.

    Scales with quantity because the machine time does: ten of a part is ten
    prints, whether they share a plate or not. The buffer is then applied to the
    whole, not per unit — a batch does not need ten separate contingencies.
    """
    if rush:
        return RUSH_LEAD_HOURS

    machine_hours = (print_minutes * Decimal(max(quantity, 1))) / Decimal(60)
    with_buffer = machine_hours * (Decimal(1) + PROMISE_BUFFER_PERCENT / Decimal(100))
    return max(with_buffer, MIN_LEAD_HOURS).quantize(Decimal("1"))
