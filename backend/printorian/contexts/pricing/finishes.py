"""The post-production options the farm sells, and what each one costs.

Scenario option 2e. Priced as labour plus an optional flat fee, so a breakdown
can *explain* a finish rather than showing an opaque surcharge.

**It lives here rather than at the API edge, and that move is the point.** The
catalogue sat in `api/routers/_pricing_spec.py` while the only things that needed
it were two request handlers. `workers/intake.py` is now a third caller — it
reprices a paid line from a cached plate — and a worker may not import the API
(`.importlinter`: `api` and `workers` are siblings). The choice was to move the
one definition or to keep a second copy in the worker, and a second copy is
exactly how V1 ended up with two calculators that quoted different numbers for
the same order; `_line_pricing.py`'s docstring is written about that failure.

Phase 2 replaces this with a managed catalogue. The shape is already what the
engine consumes, so that is a change of *where the rows come from* and not of
what a finish is.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.pricing.spec import FinishOption

#: Finishes offered to customers, by the code the configurator sends.
FINISH_CATALOGUE: dict[str, FinishOption] = {
    "raw": FinishOption(code="raw"),
    "sanded": FinishOption(code="sanded", labor_hours=Decimal("0.4")),
    "primed": FinishOption(code="primed", labor_hours=Decimal("0.6"), flat_fee=Decimal(150)),
    "painted": FinishOption(
        code="painted", labor_hours=Decimal("1.5"), flat_fee=Decimal(400), extra_days=2
    ),
}


__all__ = ["FINISH_CATALOGUE"]
