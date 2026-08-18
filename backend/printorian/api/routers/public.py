"""What an anonymous visitor may read.

One endpoint, no authentication, and nothing about any individual order — the
promo page needs figures about the farm, not about its customers.

Kept in its own router rather than added to `orders` so the absence of an auth
dependency is a property of the file, visible at a glance, instead of one route
among many that happens not to have one.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from printorian.api.deps import AppClock, DbSession
from printorian.api.farm_stats import farm_stats

router = APIRouter(prefix="/public", tags=["public"])


class FarmStatsView(BaseModel):
    """Measured facts about the farm, for the outward-facing page.

    Every figure is nullable and means it: `null` is "not enough history to say",
    which the page renders as an absence rather than as a zero. A landing page
    arguing that this shop publishes real numbers cannot round "we do not know"
    down to something that looks like a measurement.
    """

    model_config = ConfigDict(from_attributes=True)

    window_days: int
    orders_delivered: int
    on_time_percent: Decimal | None = None
    print_hours: Decimal | None = None
    failure_percent: Decimal | None = None
    #: False on a farm with no delivered orders, so the client can hide the whole
    #: proof section rather than render a row of dashes.
    has_history: bool


@router.get("/stats")
async def stats(db: DbSession, clock: AppClock) -> FarmStatsView:
    """Counted from the tables on every request.

    Not cached. The figures move slowly, but a cache here would be the first
    place a stale number could survive being wrong, and the query is four counts
    against indexed columns.
    """
    return FarmStatsView.model_validate(await farm_stats(db, now=clock.now()))
