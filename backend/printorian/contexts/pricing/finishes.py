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

**The rows now come from the settings table, and this is the default underneath
them.** `contexts.settings` declares `postprocess.operations` with
`tuple(FINISH_CATALOGUE.values())` as its default and `SettingsService.resolve_finishes()`
lays the farm's overrides over it, so an empty settings table prices exactly as
this constant always did — the whole settings context's rule, that a key with no
row is not a missing setting but the code default. «Сбросить» on the row returns
here. Nothing about *what a finish is* changed, which is why the move cost one
argument at four call sites and no change to the engine.

Two things a reader will want and should not take from the file alone. The kit
(`design/settings.html`) shows primed at 0.7 h and painted at 1.6 h against the
0.6 and 1.5 here; the code's figures are what the farm is running and the
defaults deliberately follow them, because typing the kit's in would have repriced
every quote on the day the catalogue merged. And `extra_days` is read by nothing —
grep finds its declaration, this dict, and the settings round trip that carries it
through. It is declared as the calendar days a finish adds to the promise, but
`ordering.promised_hours` takes policy, minutes, quantity and rush, and never a
finish. It is kept because a lossless round trip is cheaper than resurrecting the
number later, not because it is doing anything.
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
