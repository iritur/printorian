"""Structured logging.

JSON in production (machine-parseable, correlation-friendly), colourized console
locally. Every log line carries the correlation id of the request or job that
produced it, so a customer order can be traced across API, worker and driver.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from printorian.core.config import Settings

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def _add_correlation_id(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    correlation_id = _correlation_id.get()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    return event_dict


def _force_utf8_output() -> None:
    """Make the log stream UTF-8, whatever the console's code page says.

    A Windows console defaults to a legacy code page (cp1251 on a Russian install).
    Writing any non-ASCII log line to it — a Cyrillic path in a traceback, a Russian
    material name, an em dash — raises ``UnicodeEncodeError`` *from inside the
    logger*, so the process dies at precisely the moment it was trying to report a
    problem. ``errors="replace"`` means a stubborn byte degrades to a placeholder
    rather than taking the service down.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def configure_logging(settings: Settings) -> None:
    """Install the structlog pipeline. Call once at process start."""
    level = logging.DEBUG if settings.debug else logging.INFO

    _force_utf8_output()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
