"""Error taxonomy.

Every error carries a machine-readable ``code``. Per ADR-0012 the backend never
produces localized user-facing text: clients map ``code`` to an RU/EN message.
``details`` carries structured values for message interpolation (never a sentence).
"""

from __future__ import annotations

from typing import Any


class PrintorianError(Exception):
    """Base for every deliberate error in the system."""

    code: str = "error.internal"

    def __init__(self, error_code: str | None = None, /, **details: Any) -> None:
        """``error_code`` is positional-only *on purpose*.

        Details are arbitrary keyword arguments, and ``code`` is a natural name for
        one ("which material code?", "which line code?"). Without the ``/`` such a
        detail collides with this parameter and raises TypeError instead of the
        error the caller meant to raise — a failure that only shows up on the
        unhappy path, which is precisely where it does the most damage.
        """
        if error_code is not None:
            self.code = error_code
        self.details: dict[str, Any] = details
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, details={self.details!r})"


class ValidationError(PrintorianError):
    """Input did not satisfy a structural or semantic constraint."""

    code = "error.validation"


class NotFoundError(PrintorianError):
    """A referenced entity does not exist."""

    code = "error.not_found"


class ConflictError(PrintorianError):
    """The request conflicts with current state (duplicate, version clash)."""

    code = "error.conflict"


class PermissionDeniedError(PrintorianError):
    """The actor is authenticated but not allowed to perform this action."""

    code = "error.permission_denied"


class UnauthenticatedError(PrintorianError):
    """No valid actor could be established."""

    code = "error.unauthenticated"


class PayloadTooLargeError(PrintorianError):
    """The request body is bigger than this endpoint will accept.

    Raised while the body is still arriving, not after it has been buffered — the
    distinction is the whole point. A limit checked after ``await file.read()`` has
    already paid the memory it was meant to protect.
    """

    code = "error.payload_too_large"


class RateLimitedError(PrintorianError):
    """The caller has asked for this too often and must wait.

    Distinct from `PermissionDeniedError`: nothing is wrong with who they are, only
    with how fast they are asking. `details["retry_after_seconds"]` is what the
    handler turns into a ``Retry-After`` header, so a well-behaved client backs off
    by the right amount instead of guessing.
    """

    code = "error.rate_limited"


class DomainRuleViolationError(PrintorianError):
    """A business invariant would be broken by this operation."""

    code = "error.domain_rule"


class IntegrationError(PrintorianError):
    """An external system (printer, payment provider, carrier) failed."""

    code = "error.integration"


class ConfigurationError(PrintorianError):
    """The system is misconfigured; usually fatal at startup."""

    code = "error.configuration"
