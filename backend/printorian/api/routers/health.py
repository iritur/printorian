"""Liveness, readiness, and whether the farm's background work is still running.

Three endpoints, because they answer three different questions and a deployment
that conflates them makes bad decisions:

* ``/health`` — is this process up? Touches nothing. What a container restart
  policy reads.
* ``/health/ready`` — can this process *serve*? Names each dependency separately
  so an outage names its own cause.
* ``/health/workers`` — is the farm's background work still happening, and which
  printers is it actually connected to? Deliberately **not** part of readiness: a
  wedged sweep is not a reason to take the API out of rotation or roll a release
  back, and folding it into readiness would do exactly that. It is a monitoring
  signal, and it fails with 503 so an alert can key on it.

All three are unauthenticated, which is what a container runtime and a monitoring
probe need. They carry no farm data — dependency names, loop names and timestamps —
but they do describe the shape of the deployment, so **the storefront's edge should
not forward `/health/*`** when it is built (INFRASTRUCTURE Stage 3). The console's
proxy is on the LAN and may.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from printorian.contexts.fleet import listings as fleet_listings
from printorian.contexts.fleet import retention
from printorian.contexts.inventory import listings as inventory_listings
from printorian.contexts.production import growth
from printorian.core.db import wal_archiving_stalled
from printorian.core.driver_health import DriverStates
from printorian.core.heartbeat import Heartbeat

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. Never touches dependencies."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: every dependency this process needs in order to serve."""
    checks: dict[str, Literal["ok", "failed", "degraded"]] = {}
    unroutable = 0
    stalled_segment: str | None = None
    oversized_assignments = False
    oversized_printers = False
    oversized_materials = False

    try:
        async for session in request.app.state.database.session():
            await session.execute(text("SELECT 1"))
            # Whether ADR-0019's guarantee is actually holding. Reported here for
            # the same reason as `telemetry_partitions` below: the failure is
            # silent, unbounded, and otherwise visible only in a counter nobody
            # reads. A full backup disk broke archiving on the first farm host
            # while `/health/ready` answered 200 and `systemctl --failed` listed
            # nothing (`core.db.wal_archiving_stalled`).
            stalled_segment = await wal_archiving_stalled(session)
            # Telemetry rows that could not be routed to a month. Always zero when
            # partition provisioning is healthy, and the one condition that is
            # silently unbounded when it is not: the rows still arrive, into a
            # partition retention cannot drop and queries cannot prune. Reported
            # here so it is visible to something other than a log line nobody
            # greps for.
            unroutable = await retention.unroutable_sample_count(session)
            # The other large table, which ADR-0018 deliberately did *not*
            # partition and said would be "watched" instead. Nothing was watching
            # it: the trigger was a row count in `DATABASE-REVIEW` §9 that
            # somebody had to remember to go and measure. Two catalogue columns,
            # so this costs a probe nothing (`contexts.production.growth`).
            oversized_assignments = await growth.assignment_records_need_partitioning(session)
            # The two listings that still return everything (#45). They were left
            # unpaged because both are bounded by the size of the farm rather than
            # by history — and the growth that ends that argument does not arrive
            # as traffic, it arrives as a feature: the purchasing screen putting
            # spare parts, packaging and printers into the same listing. Nobody is
            # looking at row counts on the day a feature ships, so this looks.
            # Both counts stop at the trigger (`core.pagination.capped_count`), so
            # neither can become the expensive thing on this path.
            oversized_printers = await fleet_listings.printers_listing_oversized(session)
            oversized_materials = await inventory_listings.materials_listing_oversized(session)
        checks["database"] = "ok"
        checks["telemetry_partitions"] = "ok" if unroutable == 0 else "degraded"
        # Degraded, and it will stay degraded until the table is partitioned —
        # this reports a threshold crossed once, not a fault that comes and goes.
        # Serving is unaffected today, which is why it must not be `failed`; what
        # is affected is the cost of the fix, because converting a large table to
        # a partitioned one means copying it with writes stopped, and that price
        # only goes up.
        checks["assignment_records"] = "degraded" if oversized_assignments else "ok"
        # Degraded for the same reason again — an oversized listing serves every
        # request, it just serves a response with no ceiling on it — but unlike
        # `assignment_records` above, these two *do* clear on their own. They are a
        # live reading of a set that can shrink: retire enough printers, or
        # deactivate enough specs, and the listing is inside its bounds again and
        # says so. Nothing has been crossed once and for all here, which is why
        # they are not given that check's stays-lit behaviour.
        checks["printers_listing"] = "degraded" if oversized_printers else "ok"
        checks["materials_listing"] = "degraded" if oversized_materials else "ok"
        # Degraded rather than failed, deliberately, and for the opposite reason
        # to the relay's. Serving is unaffected — so taking this process out of
        # rotation would turn a broken *backup* into a broken *farm*, which is
        # strictly worse. But it is a slow-motion outage: `pg_wal` grows until the
        # data disk fills and writes stop, so it must be alerted on rather than
        # merely displayed.
        checks["wal_archiving"] = "ok" if stalled_segment is None else "degraded"
    except Exception:
        checks["database"] = "failed"

    # The relay is what carries the workers' events to this process's WebSocket
    # clients. Degraded rather than failed: the API serves perfectly well without
    # it, and only the live boards go quiet — so this must not take the process
    # out of rotation, but it must not go unreported either.
    relay = getattr(request.app.state, "relay", None)
    if relay is not None:
        checks["event_relay"] = "ok" if await relay.ping() else "degraded"

    if any(value == "failed" for value in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "checks": checks}
    healthy = all(value == "ok" for value in checks.values())
    return {"status": "ok" if healthy else "degraded", "checks": checks}


@router.get("/health/workers")
async def workers(request: Request, response: Response) -> dict[str, Any]:
    """Whether each worker loop has swept, and which printers it is connected to.

    A beat is recorded at the *end* of a pass, so this distinguishes a loop that
    is working from one that is merely running — the distinction
    `deploy/compose.prod.yml` correctly refused to fake with a process check.

    The drivers are reported here rather than in readiness because the API holds
    no connection state of its own: the pool lives in the worker, which publishes
    what it sees (`core.driver_health`). Per printer and never as a count — an
    outage that cannot name its own cause is one somebody has to go and find.
    """
    heartbeat: Heartbeat = request.app.state.heartbeat
    report = await heartbeat.report()
    driver_states: DriverStates = request.app.state.driver_states
    drivers = await driver_states.report()

    # **Only the loops decide the status code.** A printer being switched off is a
    # normal Tuesday on a farm, and letting it turn this endpoint red would leave
    # the workers probe permanently failing — destroying the working-versus-wedged
    # signal the endpoint exists for, and training whoever is on call to ignore
    # it. An alert about a driver keys on `drivers.*.state` and `since` in the
    # body instead.
    healthy = all(loop.is_healthy for loop in report)
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "degraded",
        "loops": {loop.loop: {"state": loop.state, "last_beat": loop.last_beat} for loop in report},
        # Empty means *nothing has been published* — no Redis, or a worker down
        # longer than the roster's window. It does not mean the farm has no
        # printers, and nothing downstream may read it as a fleet size.
        "drivers": {
            driver.printer_id: {
                "name": driver.name,
                "state": driver.state,
                "code": driver.code,
                "since": driver.since,
            }
            for driver in drivers
        },
    }
