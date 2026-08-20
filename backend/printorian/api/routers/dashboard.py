"""The farm summary — the console's first screen.

One route. Everything it returns is assembled in `_dashboard_model`, which is
where the composition across contexts is explained.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from printorian.api.deps import AppClock, DbSession, Fleet, requires
from printorian.api.routers._dashboard_model import FarmSummary, farm_summary
from printorian.contexts.identity import Permission
from printorian.contexts.ordering import Period

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    # The screen carries revenue, spend and margin beside the machine states, so
    # it needs the commercial permission and not only the production one. An
    # operator who may see the fleet reaches the fleet screen; this one is the
    # owner's view of the same farm.
    dependencies=[Depends(requires(Permission.VIEW_ALL_ORDERS))],
)


@router.get("")
async def farm(
    db: DbSession,
    fleet: Fleet,
    clock: AppClock,
    period: Period = Period.TODAY,
) -> FarmSummary:
    """Everything the dashboard draws, read against one instant.

    ``period`` moves the KPI window and the comparison behind it; the status wall,
    the schedule and the filament bars are always *now*, because "what is the farm
    doing this quarter" is not a question with an answer.
    """
    return await farm_summary(db, fleet, now=clock.now(), period=period)
