"""Measured fleet history — the hours `metric_rollups` has already summarised.

**Why this is not a route on `printers.py`, which will otherwise read as an
arbitrary split.** Two reasons, and the next person to "fix" it by moving these
under `/printers` will reintroduce both.

The first is declaration order. `printers.py` owns ``GET /printers/{printer_id}``,
and a sibling ``/printers/metrics`` is matched against that path parameter by
whichever route was declared first — a collision the framework will not warn about
and which fails as a 404 on a valid id or, worse, as a 200 for the literal string
"metrics". A separate prefix cannot collide at all.

The second is what each surface *is*. `/printers` is the registry: rows, service
cards, credentials, CRUD. This is history, read-only, gated once for both routes,
and it will grow more of the same. Keeping the registry a registry is what stops
the fleet module from becoming the place every fleet-adjacent read lands.

**`VIEW_PRODUCTION`, and these responses carry seconds and never money or energy.**
The moment `printing_seconds` is multiplied by `amortization_per_hour` or
`nominal_power_kw`, the response has become a financial one and needs
`VIEW_FINANCIALS` — CLAUDE.md keeps the two apart. Both multiplications are one
keystroke away from this file, which is exactly why the line is drawn here in the
router rather than left to whoever builds the idle-cost tile first: seconds go out,
rubles are composed elsewhere. `contexts.fleet.measures` gives the second half of
the argument — the rollup's clock is when the farm *recorded* a state, so P&L built
on it would quietly make phase 6 unable to disagree with a wrong number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from printorian.api.deps import AppClock, DbSession, Fleet, requires
from printorian.contexts.fleet import (
    FleetMetrics,
    Grain,
    MetricWindow,
    PrinterMetrics,
    fleet_metrics,
    printer_metrics,
    resolve_window,
)
from printorian.contexts.identity import Permission
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/fleet",
    tags=["fleet"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)


def metric_window(
    clock: AppClock,
    since: Annotated[datetime, Query(description="Window start, tz-aware; cut to the hour.")],
    until: Annotated[
        datetime | None,
        Query(description="End, exclusive. Defaults to and is clamped at the open hour."),
    ] = None,
    grain: Grain = Grain.HOUR,
) -> MetricWindow:
    """The window both routes read, resolved by one rule.

    A dependency rather than three parameters repeated twice: the alignment, the
    clamp and the two ceilings are the part a client has to learn, and learning it
    once should be enough. The 422s it raises (`window_empty`, `window_too_wide`,
    `naive_timestamp`) are therefore identical on both paths.
    """
    return resolve_window(since=since, until=until, grain=grain, now=clock.now())


Window = Annotated[MetricWindow, Depends(metric_window)]


@router.get("/metrics")
async def farm_metrics(db: DbSession, window: Window) -> FleetMetrics:
    """The farm's measured occupancy, hour by hour, summed across every reporter.

    The source for the kit's 7 × 24 load grid, for «Наработка за сутки» / «Простой»,
    and for the «Загрузка парка» delta. `buckets` is dense and ascending — exactly
    ``(until - since) / 1h`` entries at ``grain=hour``, exactly one at
    ``grain=total`` — and a bucket with all-null figures means *this hour was never
    summarised*, not that the farm was idle in it.
    """
    return await fleet_metrics(db, window)


@router.get("/metrics/{printer_id}")
async def machine_metrics(
    printer_id: EntityId, db: DbSession, fleet: Fleet, window: Window
) -> PrinterMetrics:
    """One machine's measured history: states, temperatures and error codes.

    Serves the fleet popup's «Загрузка за 30 дней» leader at ``grain=total``, and
    turns an alert row from a state into a pattern — «HMS_0300 ×14 за сутки» from
    `error_codes`, and `state_changes` to tell a machine that thrashed all hour
    from one that changed once.

    **The registry lookup is the first thing that happens**, and it is load-bearing
    rather than tidy. `metric_rollups.printer_id` is deliberately not a foreign key,
    so without it a typo'd id answers 200 with a dense all-null grid — which reads
    as "this machine did nothing", the invented reading ADR-0007 forbids, wearing a
    successful response.
    """
    await fleet.get(printer_id)
    return await printer_metrics(db, window, printer_id=printer_id)
