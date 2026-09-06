"""Turning the settings table into the typed objects the rest of the farm takes.

Split out of `service.py` when that file reached the 400-line gate, and split
**here** rather than at the line the counter happened to trip: everything in this
module answers "what is the farm running right now?" and returns a frozen policy
object, while what stays in `service.py` reads, writes and audits the rows
themselves. The two have different readers — a resolver is called by a pricing or
scheduling edge, the CRUD half only by the settings screen.

Every method here follows one rule, and it is the rule the whole context exists
for: **an empty table resolves to exactly the code default.** A key with no row is
not a missing setting, it is the default — so a farm that has configured nothing
prices, schedules and promises precisely as it did before the settings screen
existed. That is why each resolver lays overrides over a default object rather
than building one from the rows it happens to find.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from printorian.contexts.ordering import PromisePolicy
from printorian.contexts.pricing import CustomerTier, FinishOption, RateSnapshot
from printorian.contexts.scheduling import SchedulingPolicy
from printorian.contexts.settings import catalogue
from printorian.contexts.settings.sections import default_finishes, default_tiers

#: Settings key -> `RateSnapshot` field, for the rates the screen files outside
#: the `pricing.` section. Small on purpose: every other rate is matched by
#: prefix, and a growing list here would mean the section layout had started
#: deciding the shape of the snapshot.
_ALIASED_RATES = {"logistics.zones": "zones"}

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class SettingsResolvers:
    """The resolver half of `SettingsService`, mixed into it.

    A mixin rather than a free function taking the overrides dict, because each
    resolver calls `overrides()` itself and callers should not have to know which
    of them share a read. `overrides` is annotated here so `mypy --strict` can see
    it without this module importing the class that supplies it.
    """

    overrides: Callable[[], Awaitable[dict[str, Any]]]

    async def resolve_rates(self) -> RateSnapshot:
        """The rates a quote should be priced at right now.

        Defaults with the farm's overrides laid over them. Built with
        `dataclasses.replace` rather than by assignment because `RateSnapshot` is
        frozen — and it is frozen so that a snapshot pinned to an order cannot be
        edited afterwards, which is the guarantee ADR-0020 rests on.

        Selected by the snapshot's own **field names**, not by everything under the
        `pricing.` prefix. The prefix is a namespace on the settings screen, not a
        promise that every key beneath it is a rate: `pricing.tiers` is the customer
        price book and `RateSnapshot` has no `tiers` field, so splatting the prefix
        raised `TypeError: RateSnapshot.__init__() got an unexpected keyword
        argument 'tiers'` — an uncoded 500 on `POST /pricing/quote`, `POST /orders`
        and `POST /orders/reprice` from the moment an owner edited «Тарифы
        клиентов». Deriving the set from the dataclass is the idiom
        `resolve_scheduling` already uses below, and unlike a hand-listed skip of
        `tiers` it cannot go stale when the next table lands under `pricing.`.
        """
        overrides = await self.overrides()
        changed = {
            field.name: overrides[f"{catalogue.RATE_PREFIX}{field.name}"]
            for field in dataclasses.fields(RateSnapshot)
            if f"{catalogue.RATE_PREFIX}{field.name}" in overrides
        }
        # Rates whose key does not carry the `pricing.` prefix, mapped explicitly
        # in the shape `resolve_promise` uses below. The shipping zone table is a
        # rate — it is priced from and pinned per order — but the kit files it
        # under Логистика, and renaming it to `pricing.zones` to make the prefix
        # trick work would put it in the wrong section of the screen. It would
        # also have `rate_specs()` try to derive a second FieldSpec for the same
        # field; that it currently skips non-scalar defaults is a coincidence to
        # rely on, not a design.
        for key, field_name in _ALIASED_RATES.items():
            if key in overrides:
                changed[field_name] = overrides[key]
        return dataclasses.replace(RateSnapshot(), **changed) if changed else RateSnapshot()

    async def resolve_promise(self) -> PromisePolicy:
        """The lead-time policy a quote should promise against right now.

        The same read-edge shape as `resolve_rates`: defaults with the farm's
        overrides laid over them, so an empty table promises exactly what the farm
        always promised, and a changed `sla.min_lead_hours` moves the next quote
        and nothing already agreed.
        """
        overrides = await self.overrides()
        mapping = {
            "sla.promise_buffer_percent": "promise_buffer_percent",
            "sla.min_lead_hours": "min_lead_hours",
            "sla.rush_lead_hours": "rush_lead_hours",
        }
        changed = {
            field_name: overrides[key] for key, field_name in mapping.items() if key in overrides
        }
        return dataclasses.replace(PromisePolicy(), **changed) if changed else PromisePolicy()

    async def resolve_scheduling(self) -> SchedulingPolicy:
        """The scheduler weights a planning pass should use right now.

        Derived from the dataclass's own fields, not a hand-listed set, for the
        same reason the catalogue is: a weight added to `SchedulingPolicy`
        appears here without a second place to remember. The other `scheduling.*`
        keys — the tick interval and the wait-list behaviour — are not planner
        weights and are deliberately left out.
        """
        overrides = await self.overrides()
        changed = {
            field.name: overrides[f"scheduling.{field.name}"]
            for field in dataclasses.fields(SchedulingPolicy)
            if f"scheduling.{field.name}" in overrides
        }
        return dataclasses.replace(SchedulingPolicy(), **changed) if changed else SchedulingPolicy()

    async def resolve_int(self, key: str) -> int:
        """The resolved value of one integer setting — override, else the default."""
        overrides = await self.overrides()
        return int(overrides.get(key, catalogue.default_for(key)))

    async def resolve_tiers(self) -> dict[str, CustomerTier]:
        """The customer tiers (discount + margin override), keyed by code.

        Defaults from the loyalty ladder, with the farm's overrides laid over. The
        `from_spend` thresholds that *earn* a tier stay in `loyalty.py` — the kit's
        table shows the price book, not how a tier is earned.
        """
        overrides = await self.overrides()
        tiers = overrides.get("pricing.tiers", default_tiers())
        return {tier.code: tier for tier in tiers}

    async def resolve_finishes(self) -> dict[str, FinishOption]:
        """The postprocessing operations the farm sells right now, keyed by code.

        The same read-edge shape as `resolve_tiers`, and it exists for the same
        reason: the engine keeps receiving the catalogue inside `PriceSpec.finishes`
        and looks nothing up (ADR-0002), so the only thing that changes is where the
        rows come from. An empty table prices exactly as `FINISH_CATALOGUE` always
        did, which is the whole context's rule — a key with no row is not a missing
        setting, it is the code default.

        Deliberately **not** folded into `resolve_rates`. `RateSnapshot.snapshot_id`
        hashes its own field names, so a `finishes` field would change the hash of
        every historical snapshot rebuilt from a stored order, and
        `CachedPlates._rates_for` would then refuse every order already paid.
        ADR-0020's amendment says what is recoverable instead.
        """
        overrides = await self.overrides()
        finishes = overrides.get("postprocess.operations", default_finishes())
        return {finish.code: finish for finish in finishes}
