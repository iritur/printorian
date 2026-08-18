"""Liveness and readiness.

Readiness reports each dependency separately so an outage names its own cause.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. Never touches dependencies."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: every dependency this process needs in order to serve."""
    checks: dict[str, Literal["ok", "failed"]] = {}

    try:
        async for session in request.app.state.database.session():
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "failed"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}
