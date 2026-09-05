"""The failure side of the reliability read — numerators only, never a rate.

This module counts. It does not divide, because the denominator lives in another
context: `metric_rollups.observed_seconds`, whose own docstring calls itself "the
denominator of every percentage". The division happens one layer up, in
`api/routers/_service_reliability.py`, which is the only place the two may meet
(contexts do not import each other's internals).

**Two counts that must not collapse into one.** `causes` is a histogram over
failures somebody has named, and `uncategorised` counts the ones nobody has —
`cause IS NULL`, which is where every driver-opened failure starts. Folding those
into `FailureCause.OTHER` would put "we do not know" and "we looked, and it was
something else" in one bar, and the panel's own footer («СЛОМ ФИЛАМЕНТА :: 6 ИЗ 7
НА ВЛАЖНОМ PETG-CF») is an argument that only holds if the bars mean what they say.

**MTTR is summed over closed rows only.** ``count(*) FILTER (WHERE restored_at IS
NOT NULL)`` on both sides of the eventual fraction, so a failure still open
contributes to neither — `policies.mttr_minutes` says why, and `open_failures`
travels beside the figure so a partly-known MTTR cannot be read as a complete one.

Aggregated in SQL rather than in Python for the reason `fleet.measures` gives:
failures × machines is unbounded, and the rows exist to be counted, not carried.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.service.models import PrinterFailure
from printorian.contexts.service.policies import FailureCause
from printorian.core.ids import EntityId

class FailureTally(BaseModel):
    """What one machine's failures amount to over a window.

    No rate and no MTTR: both need something this context cannot see. What is
    here is countable from `printer_failures` alone.
    """

    printer_id: EntityId
    failures: int = 0
    #: Failures with a `restored_at`. The only ones a repair time exists for.
    closed_failures: int = 0
    #: Failures still open at the moment of the read. Reported rather than
    #: dropped: it is the size of what the MTTR beside it does not cover.
    open_failures: int = 0
    #: Summed repair time over the closed rows, in seconds. ``None`` when none
    #: were closed — `policies.mttr_minutes` turns that into no figure at all
    #: rather than into a zero-minute repair.
    repair_seconds: Decimal | None = None


class CauseCount(BaseModel):
    """One bar of the «Причины отказов» funnel."""

    cause: FailureCause
    failures: int


class FailureSummary(BaseModel):
    """Every countable thing the reliability read needs from this context."""

    #: Keyed by machine. A machine absent from this map had no failure detected in
    #: the window, which — unlike a missing rollup — really does mean zero: the
    #: farm writes a failure row or there was none.
    by_printer: dict[EntityId, FailureTally] = Field(default_factory=dict)
    causes: list[CauseCount] = Field(default_factory=list)
    #: Failures nobody has named a cause for. Kept out of `causes` on purpose.
    uncategorised: int = 0


async def failure_summary(
    db: AsyncSession, *, since: datetime, until: datetime
) -> FailureSummary:
    """Count the window's failures, per machine and per named cause."""
    return FailureSummary(
        by_printer=await _by_printer(db, since=since, until=until),
        causes=await _by_cause(db, since=since, until=until),
        uncategorised=await _uncategorised(db, since=since, until=until),
    )


def _in_window(since: datetime, until: datetime) -> tuple[ColumnElement[bool], ...]:
    """The one window predicate every statement below shares.

    Spelled once so the three counts cannot drift onto three different windows —
    which would show a histogram that does not sum to the table beside it.

    Half-open ``[since, until)``, and a failure belongs to the window by the moment
    it was **detected**. One that began before the window and was repaired inside
    it therefore belongs to the earlier report, not to this one — keying on either
    end would let a single outage be counted on two adjacent windows.
    """
    return (
        PrinterFailure.detected_at >= since,
        PrinterFailure.detected_at < until,
    )


async def _by_printer(
    db: AsyncSession, *, since: datetime, until: datetime
) -> dict[EntityId, FailureTally]:
    closed = PrinterFailure.restored_at.is_not(None)
    rows = await db.execute(
        select(
            PrinterFailure.printer_id,
            func.count().label("failures"),
            func.count().filter(closed).label("closed_failures"),
            func.count().filter(PrinterFailure.restored_at.is_(None)).label("open_failures"),
            # ``SUM`` over the closed rows, which is ``NULL`` when there are none —
            # exactly the absence `mttr_minutes` refuses to turn into a zero.
            func.sum(
                func.extract("epoch", PrinterFailure.restored_at - PrinterFailure.detected_at)
            )
            .filter(closed)
            .label("repair_seconds"),
        )
        .where(*_in_window(since, until))
        .group_by(PrinterFailure.printer_id)
    )
    return {
        row.printer_id: FailureTally(
            printer_id=row.printer_id,
            failures=row.failures,
            closed_failures=row.closed_failures,
            open_failures=row.open_failures,
            repair_seconds=(
                None if row.repair_seconds is None else Decimal(str(row.repair_seconds))
            ),
        )
        for row in rows
    }


async def _by_cause(db: AsyncSession, *, since: datetime, until: datetime) -> list[CauseCount]:
    """The funnel, named causes only, biggest bar first.

    Ordered by the count and then by the enum's own value, so two causes with the
    same count come back in a stable order rather than in whatever order the plan
    happened to produce — a funnel whose rows swap places between two identical
    reads is a panel nobody trusts.
    """
    rows = await db.execute(
        select(PrinterFailure.cause, func.count().label("failures"))
        .where(*_in_window(since, until), PrinterFailure.cause.is_not(None))
        .group_by(PrinterFailure.cause)
        .order_by(func.count().desc(), PrinterFailure.cause)
    )
    return [CauseCount(cause=row.cause, failures=row.failures) for row in rows]


async def _uncategorised(db: AsyncSession, *, since: datetime, until: datetime) -> int:
    count = await db.scalar(
        select(func.count()).where(*_in_window(since, until), PrinterFailure.cause.is_(None))
    )
    return int(count or 0)


__all__ = ["CauseCount", "FailureSummary", "FailureTally", "failure_summary"]
