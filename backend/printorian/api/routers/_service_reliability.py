"""Failures ÷ observed hours — the one place those two may meet.

`contexts.service` counts failures and never divides; `contexts.fleet` owns
`metric_rollups.observed_seconds` and never counts failures. Neither may import
the other's internals, so the join lives here, exactly as `_dashboard_model.py`
puts the join where a printer's zone meets a material's committed mass.

**The denominator is what was observed, never the roster.** That is CLAUDE.md §1
and it is the whole reason this file is worth reading twice. `observed_by_printer`
returns a sparse map, a machine missing from it was never summarised in the
window, and `failures_per_1000_hours` answers ``None`` for that machine rather than
a rate. Every registered printer still gets a *row* — dropping it would silently
shrink the table and make the farm look better the worse its monitoring got — but
the row carries nulls, and the response says how many machines actually reported.

**There is deliberately no fleet-wide rate and no «Готовность парка» tile.** The
kit draws both, and both would have to be failures over some farm-wide denominator.
The only honest one is the sum of the observed seconds, which is not what a reader
would take «Готовность парка» to mean, and the tempting one is ``machines × window``
— the fabricated denominator `measures.py` already refuses to compute for load.
Summing per-machine rates is worse again: it weights a machine watched for ten
minutes the same as one watched all month.

**Seconds and counts. No rubles, no kilowatt-hours.** `Printer.amortization_per_hour`
and `nominal_power_kw` are on the `PrinterView` this module already holds, so the
kit's «ПОТЕРЯ 3 820 ₽» is one multiplication away — and would turn a response a
`VIEW_PRODUCTION` role reads into a financial one. `tests/api/test_service_api.py`
asserts the key set for that reason.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import (
    FleetService,
    MetricWindow,
    PrinterView,
    observed_by_printer,
)
from printorian.contexts.service import (
    CauseCount,
    FailureTally,
    failure_summary,
    failures_per_1000_hours,
    mttr_minutes,
)
from printorian.core.ids import EntityId
from printorian.drivers import PrinterState


class ReliabilityRow(BaseModel):
    """One machine's reliability over the window, with its absences intact."""

    printer_id: EntityId
    printer_name: str
    #: Live state, so a row reading "no failures" can be told apart from a row
    #: about a machine that is sitting in `ERROR` right now with an open failure.
    state: PrinterState

    failures: int = 0
    #: Failures the farm has seen the end of. The only ones with a repair time.
    closed_failures: int = 0
    #: Still open. On the wire beside `mttr_minutes` so a partly-known repair
    #: time cannot be read as a complete one.
    open_failures: int = 0

    #: ``None`` when no `metric_rollups` row covered this machine in the window.
    #: Not zero: zero would be a measurement, and this is the absence of one.
    observed_seconds: Decimal | None = None
    #: ``None`` whenever `observed_seconds` is. A real ``0`` means the farm watched
    #: this machine and it did not break, which is a different fact.
    failures_per_1000_hours: Decimal | None = None
    #: ``None`` until at least one failure on this machine has been closed.
    mttr_minutes: Decimal | None = None


class ReliabilityReport(BaseModel):
    """«Надёжность» for the whole roster, over one window."""

    window: MetricWindow
    #: One row per registered machine, ordered by name so the table is stable
    #: between reads. A machine with neither failures nor rollups is present and
    #: null rather than absent — see the module docstring.
    rows: list[ReliabilityRow] = Field(default_factory=list)

    #: The «Причины отказов» funnel over named causes only.
    causes: list[CauseCount] = Field(default_factory=list)
    #: Failures nobody has named a cause for — every driver-opened one starts here.
    #: Reported apart from `FailureCause.OTHER` on purpose (ADR-0007).
    uncategorised: int = 0

    #: Machines with at least one `metric_rollups` row in the window, and machines
    #: on the roster. The pair is the coverage a reader needs in order to know how
    #: much of the table below is nulls — the same job `printers_reporting` does on
    #: a fleet bucket.
    printers_reporting: int = 0
    printers_listed: int = 0


async def reliability_report(
    db: AsyncSession, fleet: FleetService, window: MetricWindow
) -> ReliabilityReport:
    """Assemble the roster, the failures and the observed hours into one answer."""
    observed = await observed_by_printer(db, window)
    failures = await failure_summary(db, since=window.since, until=window.until)
    roster = await _roster(fleet, observed=observed, failures=failures.by_printer)

    return ReliabilityReport(
        window=window,
        rows=[
            _row(printer, observed.get(printer.id), failures.by_printer.get(printer.id))
            for printer in roster
        ],
        causes=failures.causes,
        uncategorised=failures.uncategorised,
        # Counted over the machines on the table rather than over the whole map:
        # a rollup row for a printer that has since been deleted would otherwise
        # inflate the coverage figure above the number of rows it describes.
        printers_reporting=sum(1 for printer in roster if printer.id in observed),
        printers_listed=len(roster),
    )


async def _roster(
    fleet: FleetService,
    *,
    observed: dict[EntityId, Decimal],
    failures: dict[EntityId, FailureTally],
) -> list[PrinterView]:
    """Active machines, plus any retired one this window actually has evidence about.

    Retired machines are not listed wholesale: a farm that has replaced its printers
    twice would otherwise show a table mostly made of null rows about hardware that
    left. But one retired *during* the window took its failures and its measured
    hours with it, and dropping those would quietly improve the period — the same
    shrinking denominator this module exists to refuse, arriving through the roster
    instead of through the arithmetic.
    """
    table = await fleet.table(include_inactive=True)
    return [
        printer
        for printer in table.rows
        if printer.is_active or printer.id in observed or printer.id in failures
    ]


def _row(
    printer: PrinterView, observed_seconds: Decimal | None, tally: FailureTally | None
) -> ReliabilityRow:
    """One row, with both kinds of absence kept apart.

    A machine with no `tally` genuinely had no failure in the window — the farm
    writes a row when something breaks or there was nothing to write — so the
    counts are real zeroes. A machine with no `observed_seconds` was never
    summarised, and the rate stays ``None``.
    """
    failures = tally.failures if tally else 0
    closed = tally.closed_failures if tally else 0

    return ReliabilityRow(
        printer_id=printer.id,
        printer_name=printer.name,
        state=printer.state,
        failures=failures,
        closed_failures=closed,
        open_failures=tally.open_failures if tally else 0,
        observed_seconds=observed_seconds,
        failures_per_1000_hours=failures_per_1000_hours(failures, observed_seconds),
        mttr_minutes=mttr_minutes(tally.repair_seconds if tally else None, closed),
    )


__all__ = ["ReliabilityReport", "ReliabilityRow", "reliability_report"]
