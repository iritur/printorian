"""The failure record over HTTP: what broke, when it came back, and how often.

**A prefix of its own, not extra routes on `printers.py` or `fleet.py`**, and the
first reason is the one `fleet.py` already writes down: `printers.py` owns
``GET /printers/{printer_id}``, so a sibling ``/printers/failures`` is matched
against that path parameter by whichever route was declared first — a collision
FastAPI does not warn about. The second is what the surface *is*. `/printers` is
the registry and `/fleet` is measured history; this is neither. It is work: a
person or a sweep opens a record, an observation closes it, and somebody's
judgement names the cause.

**Two permissions.** Reading reliability is `VIEW_PRODUCTION`, the same gate the
measured-history routes carry. Writing to the record is `OPERATE_PRINTER`, which
the operator role already holds — deliberately **no new `Permission` member**: the
service board those would gate is not in this slice, and a permission with nothing
behind it is a hook with no handler.

**Seconds and counts leave here; rubles do not.** `fleet.py` draws the same line
for the same reason and it is worth repeating at the file that would break it:
`Printer.amortization_per_hour` and `nominal_power_kw` are on the view
`_service_reliability` already holds, so the kit's «ПОТЕРЯ 3 820 ₽» is one
multiplication from this router — and would put money behind a production
permission. It belongs behind `VIEW_FINANCIALS`, on a different route, composed
somewhere else.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from printorian.api.deps import AppClock, CurrentActor, DbSession, Fleet, ServiceDeskDep, requires
from printorian.api.routers._service_reliability import ReliabilityReport, reliability_report
from printorian.contexts.fleet import Grain, MetricWindow, resolve_window
from printorian.contexts.identity import Permission
from printorian.contexts.service import (
    FailureOrigin,
    FailureView,
    NameCause,
    RecordFailure,
    RestoreFailure,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/service",
    tags=["service"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

_OPERATE = Depends(requires(Permission.OPERATE_PRINTER))


def reliability_window(
    clock: AppClock,
    since: Annotated[datetime, Query(description="Window start, tz-aware; cut to the hour.")],
    until: Annotated[
        datetime | None,
        Query(description="End, exclusive. Defaults to and is clamped at the open hour."),
    ] = None,
) -> MetricWindow:
    """The same window rule `/fleet/metrics` uses, so the 422s are the same 422s.

    `resolve_window` rather than a second alignment written here: a client that
    learned `window_empty`, `window_too_wide` and `naive_timestamp` on the metrics
    routes must not have to learn a second dialect of them, and — the part that
    would actually go wrong — the denominator this report divides by is read from
    `metric_rollups` on exactly the window those routes serve. Two alignment rules
    would let the failures and the hours be counted over two different windows.

    Pinned to ``grain=total``: this report is one figure per machine, so there is
    no hourly shape to ask for and no reason to expose the 744-bucket ceiling.
    """
    return resolve_window(since=since, until=until, grain=Grain.TOTAL, now=clock.now())


Window = Annotated[MetricWindow, Depends(reliability_window)]


@router.get("/reliability")
async def reliability(db: DbSession, fleet: Fleet, window: Window) -> ReliabilityReport:
    """«Надёжность»: failures over the hours the farm actually watched each machine.

    Declared **above** every ``/service/{...}`` route below on purpose — FastAPI
    matches in declaration order, and `api/routers/jobs.py` carries a test whose
    only job is to notice when that ordering is disturbed.

    Every registered machine gets a row. One that was never summarised carries a
    null denominator and a null rate rather than a zero, because zero would read as
    perfect reliability for precisely the machine nobody is watching.
    """
    return await reliability_report(db, fleet, window)


@router.post("/failures", status_code=201, dependencies=[_OPERATE])
async def record_failure(
    body: RecordFailure, fleet: Fleet, desk: ServiceDeskDep, actor: CurrentActor
) -> FailureView:
    """Somebody at the machine records that it stopped working.

    **The registry lookup is the first thing that happens**, and it is load-bearing
    rather than tidy: `ServiceDesk.record` trusts the id it is given, so without
    this a typo'd `printer_id` would be refused by the foreign key as a 500 at
    best, and — if the id happened to exist — would file a failure against the
    wrong machine. `fleet.get` raises `error.fleet.not_found`, which is the 404 the
    same mistake gets on every other printer-scoped route.

    `origin` is not in the body. It is `PERSON` here by construction, because this
    is the route a person calls; `DRIVER` is written only by `workers/service.py`,
    from a state a machine reported. Letting a client claim the machine said
    something would make «СООБЩИЛ ДРАЙВЕР» a badge anyone can print.
    """
    await fleet.get(body.printer_id)
    return await desk.record(
        body.printer_id,
        FailureOrigin.PERSON,
        cause=body.cause,
        error_code=body.error_code,
        detected_at=body.detected_at,
        note=body.note,
        recorded_by=actor.user_id,
    )


@router.post("/failures/{failure_id}/restore", dependencies=[_OPERATE])
async def restore_failure(
    failure_id: EntityId, body: RestoreFailure, desk: ServiceDeskDep
) -> FailureView:
    """Record the observation in which the machine was working again.

    Refuses a second restore (`error.service.already_restored`) and one earlier
    than the detection (`error.service.restored_before_detected`). The first is the
    irreversible-ish path: `restored_at` is the single measurement of how long this
    machine was down, and overwriting it lengthens a repair that already ended.
    """
    return await desk.restore(failure_id, body.restored_at)


@router.post("/failures/{failure_id}/cause", dependencies=[_OPERATE])
async def name_cause(
    failure_id: EntityId, body: NameCause, desk: ServiceDeskDep, actor: CurrentActor
) -> FailureView:
    """Name the cause of a failure — the route a driver-opened one gets one by.

    There is no route that guesses. `bambu.print_error.{code}` is recorded verbatim
    on the row and the farm holds no table turning a vendor code into «слом
    филамента»; a person looks at the machine, and this is where their answer goes.
    """
    return await desk.set_cause(failure_id, body.cause, actor.user_id)
