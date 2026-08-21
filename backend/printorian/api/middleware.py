"""Request-scoped logging context, and one line per request.

`core.logging` has always said that "every log line carries the correlation id of
the request or job that produced it, so a customer order can be traced across API,
worker and driver". It had the machinery — a context variable and a structlog
processor — and nothing ever called `set_correlation_id`, so every line carried
nothing. This is the half that was missing.

It also emits the access log. There was none: the only latency signal in production
was PostgreSQL's `log_min_duration_statement`, which sees a slow *query* and cannot
see a slow *request* — and the request that mattered most was slow for a reason no
query would ever show, namely seconds of mesh analysis on the event loop
(`core.cpu`). A request line with a duration is what makes that visible next time.

The id comes from the caller's ``X-Request-ID`` when there is one, so a trace begun
at the reverse proxy continues here instead of restarting, and it is echoed back on
the response so a person reporting a problem can quote it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from printorian.core.errors import PayloadTooLargeError
from printorian.core.ids import new_id
from printorian.core.logging import set_correlation_id

logger = structlog.get_logger(__name__)

#: Header carrying a trace across a proxy hop, in and out.
REQUEST_ID_HEADER = "X-Request-ID"

#: Longest inbound id accepted. An id is a correlation key, not a payload: without
#: a bound, a caller chooses how much of every log line in the request they own.
_MAX_ID_LENGTH = 128

#: Paths whose success is not worth a line. The container healthcheck runs every
#: few seconds for ever, and a log in which nine of every ten lines are `/health`
#: is a log nobody reads. Failures are still logged — that is the interesting case.
_QUIET_PATHS = frozenset({"/health", "/health/ready"})

#: Below this, a response is ordinary; at or above it, worth `warning`.
_CLIENT_ERROR = 400
_SERVER_ERROR = 500

#: Spelled as a literal for the same reason `api.errors` does: Starlette renamed
#: the constant and the deprecation shim warns on the old name.
_TOO_LARGE = 413


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Binds a correlation id to the request, logs its outcome, echoes the id."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = _incoming_id(request) or str(new_id())
        # Set *before* `call_next`, which matters: Starlette runs the rest of the
        # application in a child task, and a task copies the context it is given
        # at creation. Binding afterwards would leave every line the handler
        # writes without the id — the exact hole this middleware exists to close.
        set_correlation_id(correlation_id)
        # structlog's own contextvars as well, so a log written inside a service —
        # which knows nothing about HTTP — carries the id too.
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = correlation_id
            # Logged here rather than after the `finally`, or the access line
            # itself would be written with the id already unbound.
            _log_response(request, response, started)
            return response
        except Exception:
            # The error handlers turn a `PrintorianError` into a response, so
            # anything arriving here is unhandled. Logged with the id before it
            # is re-raised, or the one line naming the failure has no trace on it.
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=_elapsed_ms(started),
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
            set_correlation_id(None)


def _incoming_id(request: Request) -> str | None:
    raw = request.headers.get(REQUEST_ID_HEADER, "").strip()
    if not raw or len(raw) > _MAX_ID_LENGTH:
        return None
    # Control characters in a log line are how a forged id fakes a second entry.
    return raw if raw.isprintable() else None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _log_response(request: Request, response: Response, started: float) -> None:
    status = response.status_code
    if status < _CLIENT_ERROR and request.url.path in _QUIET_PATHS:
        return

    fields = {
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "duration_ms": _elapsed_ms(started),
    }
    if status >= _SERVER_ERROR:
        logger.error("request", **fields)
    elif status >= _CLIENT_ERROR:
        logger.warning("request", **fields)
    else:
        logger.info("request", **fields)


class BodySizeLimitMiddleware:
    """Refuses an over-sized request body while it is still arriving.

    The endpoints used to check the size *after* reading:
    ``data = await file.read()`` and then ``if len(data) > max_bytes``. By then
    the memory the limit exists to protect has already been spent — and the
    settings comment beside `max_upload_bytes` claimed the opposite, that a mesh
    above the limit is "refused before it is read into memory rather than after".
    This is what makes that sentence true.

    It has to live at the ASGI level to be worth anything. By the time a route
    handler runs, `python-multipart` has parsed the whole body, so no check inside
    a handler — however early — can decline to buffer it.

    Two paths, because a client chooses which one it uses, and they end
    differently for a reason worth stating rather than rediscovering:

    * **Declared.** Almost every real client sends ``Content-Length`` — a browser
      always does for a ``FormData`` upload — so this is the path that matters.
      The request is answered **here**, with the API's ordinary error envelope and
      a 413, before the application is called at all and before a byte of body is
      requested.
    * **Chunked.** A client that declares nothing is counted as it streams and cut
      off the moment the running total passes the ceiling. That one surfaces as a
      **400**, not a 413: FastAPI wraps body parsing in ``except Exception`` and
      turns anything raised there into "there was an error parsing the body", so
      the status is not ours to choose once the handler has started reading. The
      memory is still bounded, which is what the guard is for.
    """

    #: Room above `max_upload_bytes` for multipart framing — boundaries, part
    #: headers, and the other form fields travelling with the file. Generous: this
    #: is a ceiling on *buffering*, and the exact-size rule is the endpoint's own
    #: check on the decoded part.
    OVERHEAD_BYTES = 1024 * 1024

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.limit = max_bytes + self.OVERHEAD_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.limit:
            # Answered without ever calling the application, which is the whole
            # point: nothing downstream reads the body, so nothing buffers it.
            await self._refuse(scope, send, size=declared)
            return

        received = 0

        async def guarded() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise PayloadTooLargeError(
                        "error.payload_too_large", size=received, limit=self.limit
                    )
            return message

        await self.app(scope, guarded, send)

    async def _refuse(self, scope: Scope, send: Send, *, size: int) -> None:
        """Send a 413 in the API's error envelope, by hand.

        By hand because this runs outside the exception middleware, so a raise
        here would come back as a bare 500 from the server rather than as the
        shape every other error has (`api.errors`). Clients map `code`, and one
        endpoint answering a different shape is how a client's error handling
        develops a special case (ADR-0012).
        """
        body = json.dumps(
            {
                "code": PayloadTooLargeError.code,
                "details": {"size": size, "limit": self.limit},
            }
        ).encode()
        logger.warning(
            "request_body_too_large",
            path=scope.get("path"),
            size=size,
            limit=self.limit,
        )
        await send(
            {
                "type": "http.response.start",
                "status": _TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    # The caller is not going to send the rest, and neither
                    # keep-alive nor a half-read body is worth the ambiguity.
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


__all__ = ["REQUEST_ID_HEADER", "BodySizeLimitMiddleware", "CorrelationIdMiddleware"]
