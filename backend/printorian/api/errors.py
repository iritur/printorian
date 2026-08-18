"""Mapping of domain errors onto HTTP.

The body is always ``{"code": ..., "details": {...}}``. The backend never sends a
localized sentence — clients render ``code`` from their RU/EN catalogue (ADR-0012).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from printorian.core.errors import (
    ConfigurationError,
    ConflictError,
    DomainRuleViolationError,
    IntegrationError,
    NotFoundError,
    PermissionDeniedError,
    PrintorianError,
    UnauthenticatedError,
    ValidationError,
)

logger = structlog.get_logger(__name__)

# 422 is spelled as a literal: Starlette renamed the constant
# (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT) and the deprecation shim warns.
_UNPROCESSABLE = 422

_STATUS_BY_TYPE: list[tuple[type[PrintorianError], int]] = [
    (ValidationError, _UNPROCESSABLE),
    (UnauthenticatedError, status.HTTP_401_UNAUTHORIZED),
    (PermissionDeniedError, status.HTTP_403_FORBIDDEN),
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (DomainRuleViolationError, _UNPROCESSABLE),
    (IntegrationError, status.HTTP_502_BAD_GATEWAY),
    (ConfigurationError, status.HTTP_500_INTERNAL_SERVER_ERROR),
]


def status_for(error: PrintorianError) -> int:
    for error_type, code in _STATUS_BY_TYPE:
        if isinstance(error, error_type):
            return code
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def error_body(error: PrintorianError) -> dict[str, Any]:
    return {"code": error.code, "details": error.details}


#: Constraint values worth telling a person about ("at least 10 characters").
#: Taken by name so nothing else from Pydantic's ``ctx`` reaches the response —
#: ``ctx`` can hold exception *instances*, which are not JSON-serializable and
#: would turn a 422 into a 500 inside the error handler itself.
_LIMIT_KEYS = ("min_length", "max_length", "ge", "le", "gt", "lt")


def validation_body(exc: RequestValidationError) -> dict[str, Any]:
    """Render a request-validation failure in the standard envelope.

    Pydantic's own shape is ``{"detail": [...]}`` with no ``code``, so without
    this every rejected form field reached the client as a generic internal
    error — the API's one error contract, broken exactly where a user is most
    likely to make a mistake.

    The code is built from field and rule (``error.validation.password.string_too_short``)
    so a client's prefix fallback degrades it on its own: an unknown rule renders
    the field's message, an unknown field renders the generic one. No exhaustive
    mapping has to be kept in step on either side.
    """
    errors = exc.errors()
    first: dict[str, Any] = errors[0] if errors else {}

    # loc is ("body", "password") — the field is the last string in it. Nested
    # models give ("body", "lines", 0, "material_code"), where the index is an
    # int and the field is still the last string.
    location = [part for part in first.get("loc", ()) if isinstance(part, str)]
    field = location[-1] if location and location[-1] not in ("body", "query", "path") else ""
    rule = str(first.get("type", "")) or "invalid"

    code = ".".join(part for part in ("error.validation", field, rule) if part)

    details: dict[str, Any] = {"field": field, "rule": rule}
    context = first.get("ctx")
    if isinstance(context, dict):
        for key in _LIMIT_KEYS:
            if key in context and isinstance(context[key], int | float | str):
                details["limit"] = context[key]
                break

    # Every offending field, so a form can mark more than the first one.
    fields = [
        parts[-1]
        for error in errors
        if (parts := [p for p in error.get("loc", ()) if isinstance(p, str)])
        and parts[-1] not in ("body", "query", "path")
    ]
    if fields:
        details["fields"] = fields

    return {"code": code, "details": details}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PrintorianError)
    async def _handle(request: Request, exc: PrintorianError) -> JSONResponse:
        http_status = status_for(exc)
        if http_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error(
                "unhandled_domain_error",
                code=exc.code,
                path=request.url.path,
                exc_info=exc,
            )
        return JSONResponse(status_code=http_status, content=error_body(exc))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=_UNPROCESSABLE, content=validation_body(exc))
