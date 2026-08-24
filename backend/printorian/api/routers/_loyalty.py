"""Resolving a caller's loyalty tier, once, at the edge.

ADR-0002 says the engine is given its rates and looks nothing up. A tier is a
rate, so it is resolved here — in the delivery layer, where a database is allowed
— and passed in.

Every path that produces a price goes through this: the configurator's quote, the
option preview, the checkout re-price and the order itself. That is not tidiness.
The account screen shows a badge reading «Silver · −4%», and a badge that is not
subtracted anywhere is a lie printed in the farm's own colours. Four call sites
mean four chances to forget, so there is one function and it is used by all of
them.

Anonymous callers get ``None``, and the engine's own default applies: the standard
price book, no discount. A price shown to somebody who is not signed in is
therefore the highest one they could pay, which is the safe direction — signing in
can only lower it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity import Actor
from printorian.contexts.ordering import spent
from printorian.contexts.pricing import CustomerTier, tier_for_spend


async def tier_for(
    db: AsyncSession,
    actor: Actor | None,
    tiers: dict[str, CustomerTier] | None = None,
) -> CustomerTier | None:
    """The price book this caller has earned, or ``None`` for the standard one.

    `tiers` is the settings-resolved price book, when the caller has one: the
    loyalty ladder still decides *which* code the lifetime spend earns (the
    `from_spend` thresholds), and the resolved tier overrides the discount and
    margin that code carries.
    """
    if actor is None:
        return None
    base = tier_for_spend(await spent(db, actor.user_id))
    if tiers and base.code in tiers:
        return tiers[base.code]
    return base
