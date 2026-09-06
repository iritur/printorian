"""Opening, closing and naming a failure.

Four operations, and the interesting one is the pair that must not be allowed to
happen twice.

**A restore is written once.** `restored_at` is the only measurement of how long
the machine was down, and a second restore would overwrite it with a later moment
— silently lengthening a repair that already ended. So a restored row refuses, and
the refusal carries the moment already recorded so the caller can see what it was
about to replace.

**This service does not look a printer up.** `record` takes a `printer_id` and
trusts it, because the 404 belongs to the route, which composes `Fleet` and checks
the registry first (`api/routers/fleet.py` states the rule: without that check a
typo'd id answers 200 over an empty record). Reaching into `fleet` from here would
also cross a boundary `check_context_isolation.py` refuses.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.service.models import PrinterFailure
from printorian.contexts.service.policies import FailureCause, FailureOrigin
from printorian.contexts.service.schemas import FailureView
from printorian.core.clock import Clock
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId


class ServiceDesk:
    """The failure record, as the farm writes to it."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._db = session
        self._clock = clock

    async def record(
        self,
        printer_id: EntityId,
        origin: FailureOrigin,
        *,
        cause: FailureCause | None = None,
        error_code: str | None = None,
        detected_at: datetime | None = None,
        note: str | None = None,
        recorded_by: EntityId | None = None,
    ) -> FailureView:
        """Open a failure against a machine.

        `detected_at` defaults to the clock, which is right for a person standing
        at the machine and wrong for the sweep — so the sweep passes the printer's
        own `last_seen_at` instead. Defaulting it here rather than requiring it
        keeps that a deliberate act at the one call site that has a better answer.

        `cause` stays ``None`` for anything the driver opened. See
        `policies.FailureCause`: the farm has no table turning a vendor error code
        into a cause, and inventing the mapping would fill the «Причины отказов»
        funnel with a measurement nobody made.
        """
        failure = PrinterFailure(
            printer_id=printer_id,
            origin=origin,
            cause=cause,
            error_code=error_code,
            detected_at=detected_at or self._clock.now(),
            note=note,
            recorded_by=recorded_by,
        )
        self._db.add(failure)
        await self._db.flush()
        return FailureView.model_validate(failure)

    async def restore(self, failure_id: EntityId, at: datetime) -> FailureView:
        """Record the observation in which the machine was working again.

        Both refusals are codes with structured details (ADR-0012). The second one
        is also held by a CHECK on the table, which is the half that survives a
        writer that is not this method.
        """
        failure = await self._failure(failure_id)
        if failure.restored_at is not None:
            raise DomainRuleViolationError(
                "error.service.already_restored",
                failure_id=str(failure_id),
                restored_at=failure.restored_at.isoformat(),
            )
        if at < failure.detected_at:
            raise DomainRuleViolationError(
                "error.service.restored_before_detected",
                failure_id=str(failure_id),
                detected_at=failure.detected_at.isoformat(),
                restored_at=at.isoformat(),
            )
        failure.restored_at = at
        await self._db.flush()
        return FailureView.model_validate(failure)

    async def set_cause(
        self, failure_id: EntityId, cause: FailureCause, by: EntityId | None = None
    ) -> FailureView:
        """Name the cause of a failure — the route a driver-opened one gets one by.

        Deliberately allowed on a failure that already carries a cause: the first
        answer is a person's judgement, not a measurement, and correcting it is
        ordinary. `recorded_by` moves to whoever named it, because after this the
        row's most consequential field is theirs.
        """
        failure = await self._failure(failure_id)
        failure.cause = cause
        if by is not None:
            failure.recorded_by = by
        await self._db.flush()
        return FailureView.model_validate(failure)

    async def open_for(self, printer_ids: Sequence[EntityId]) -> list[FailureView]:
        """The currently-open failures of these machines.

        Empty sequence in, empty list out, without a query: ``IN ()`` is not valid
        SQL everywhere and a sweep on a farm with no machines should not depend on
        the dialect's opinion of it.
        """
        if not printer_ids:
            return []
        rows = await self._db.scalars(
            select(PrinterFailure).where(
                PrinterFailure.printer_id.in_(printer_ids),
                PrinterFailure.restored_at.is_(None),
            )
        )
        return [FailureView.model_validate(row) for row in rows]

    async def _failure(self, failure_id: EntityId) -> PrinterFailure:
        failure = await self._db.get(PrinterFailure, failure_id)
        if failure is None:
            raise NotFoundError("error.service.failure_not_found", failure_id=str(failure_id))
        return failure


__all__ = ["ServiceDesk"]
