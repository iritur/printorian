"""Liveness, readiness, and whether the farm's background work is still running.

Three endpoints, because they answer three different questions and a deployment
that conflates them makes bad decisions:

* ``/health`` — is this process up? Touches nothing. What a container restart
  policy reads.
* ``/health/ready`` — can this process *serve*? Names each dependency separately
  so an outage names its own cause.
* ``/health/workers`` — is the farm's background work still happening? Deliberately
  **not** part of readiness: a wedged sweep is not a reason to take the API out of
  rotation or roll a release back, and folding it into readiness would do exactly
  that. It is a monitoring signal, and it fails with 503 so an alert can key on it.

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

from printorian.contexts.fleet import retention
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

    try:
        async for session in request.app.state.database.session():
            await session.execute(text("SELECT 1"))
            # Telemetry rows that could not be routed to a month. Always zero when
            # partition provisioning is healthy, and the one condition that is
            # silently unbounded when it is not: the rows still arrive, into a
            # partition retention cannot drop and queries cannot prune. Reported
            # here so it is visible to something other than a log line nobody
            # greps for.
            unroutable = await retention.unroutable_sample_count(session)
        checks["database"] = "ok"
        checks["telemetry_partitions"] = "ok" if unroutable == 0 else "degraded"
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
    """Whether each worker loop has swept within its own window.

    A beat is recorded at the *end* of a pass, so this distinguishes a loop that
    is working from one that is merely running — the distinction
    `deploy/compose.prod.yml` correctly refused to fake with a process check.
    """
    heartbeat: Heartbeat = request.app.state.heartbeat
    report = await heartbeat.report()

    if not all(loop.is_healthy for loop in report):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if all(loop.is_healthy for loop in report) else "degraded",
        "loops": {loop.loop: {"state": loop.state, "last_beat": loop.last_beat} for loop in report},
    }
