"""«Надёжность»: failures over the hours the farm actually watched.

Every test here is one question in different clothes — *can a machine nobody
measured be told apart from a machine that was measured and did not break?* — and
it is the question root CLAUDE.md §1 is written about. The flattering direction is
the dangerous one: a `0` where the answer is "nobody looked" makes the
worst-monitored printer the healthiest row in the table, and it does it silently.

Read through `reliability_report` rather than through `contexts.service` alone,
because the division is the thing under test and it deliberately does not live in
either context. `contexts.service` counts and `contexts.fleet` measures; the file
that divides is `api/routers/_service_reliability.py`, and it is the only place any
of these numbers can go wrong.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.api.routers._service_reliability import (
    ReliabilityReport,
    ReliabilityRow,
    reliability_report,
)
from printorian.contexts.fleet import FleetService
from printorian.contexts.service import FailureCause, ServiceDesk
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.secrets import SecretBox
from tests.unit._measure_support import FULL_HOUR, HOUR, summarised
from tests.unit._service_support import WINDOW, a_failure, a_printer

KEY = "a-development-secret-key-not-for-production"


@pytest.fixture
def fleet(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> FleetService:
    return FleetService(db_session, clock, bus, SecretBox(KEY))


@pytest.fixture
def desk(db_session: AsyncSession, clock: FixedClock) -> ServiceDesk:
    return ServiceDesk(db_session, clock)


async def report(db: AsyncSession, fleet: FleetService) -> ReliabilityReport:
    return await reliability_report(db, fleet, WINDOW)


def row_for(answer: ReliabilityReport, printer_id: EntityId) -> ReliabilityRow:
    return next(row for row in answer.rows if row.printer_id == printer_id)


# ------------------------------------------------- the denominator that was observed


async def test_a_machine_with_no_summarised_hours_has_no_reliability_figure(
    db_session: AsyncSession, fleet: FleetService
) -> None:
    """The single most important assertion in this slice.

    No `metric_rollups` row covered this machine, so there are no hours to divide
    by. A `0` here would read as "never fails" for exactly the printer the farm is
    not watching, and the worse the coverage got the healthier the table would look
    — CLAUDE.md §1's flattering denominator, arriving through an empty map rather
    than through a roster.
    """
    printer = await a_printer(db_session, name="unwatched")

    row = row_for(await report(db_session, fleet), printer)

    assert row.observed_seconds is None
    assert row.failures_per_1000_hours is None
    assert row.failures == 0


async def test_a_machine_that_was_measured_and_never_failed_reads_zero_not_null(
    db_session: AsyncSession, fleet: FleetService
) -> None:
    """The other direction, and the reason the one above is not simply "null always".

    An hour was summarised and nothing broke in it. That is a *measurement* of
    perfect reliability, and collapsing it into the same `null` as "nobody looked"
    would throw away the farm's actual good news along with its blind spots.
    """
    printer = await a_printer(db_session, name="watched")
    await summarised(db_session, printer, HOUR, idle_seconds=FULL_HOUR)

    row = row_for(await report(db_session, fleet), printer)

    assert row.observed_seconds == FULL_HOUR
    assert row.failures_per_1000_hours == Decimal("0.00")


async def test_the_denominator_is_observed_seconds_and_not_the_window(
    db_session: AsyncSession, fleet: FleetService, desk: ServiceDesk
) -> None:
    """Two machines, one failure each, and two different amounts of watching.

    The half-covered machine broke once in half the observed time, so its rate is
    double. If the denominator were the window — or the roster, or `machines ×
    hours` — the two rows would be identical, and the machine the farm barely
    watched would be indistinguishable from the one it watched all along.
    """
    watched = await a_printer(db_session, name="A-watched")
    glimpsed = await a_printer(db_session, name="B-glimpsed")
    await summarised(db_session, watched, HOUR, observed_seconds=FULL_HOUR)
    await summarised(db_session, glimpsed, HOUR, observed_seconds=FULL_HOUR / 2)
    await a_failure(desk, watched)
    await a_failure(desk, glimpsed)

    answer = await report(db_session, fleet)

    assert row_for(answer, watched).failures_per_1000_hours == Decimal("1000.00")
    # Double, from the same one failure, because half as much of it was watched.
    assert row_for(answer, glimpsed).failures_per_1000_hours == Decimal("2000.00")


# --------------------------------------------------------------------------- MTTR


async def test_mttr_ignores_failures_that_have_not_been_repaired(
    db_session: AsyncSession, fleet: FleetService, desk: ServiceDesk
) -> None:
    """An open failure counts on neither side of the fraction.

    It has no repair duration yet, and the tempting substitute — ``now -
    detected_at`` — would make MTTR climb while nobody is working on anything, which
    is a number the farm never measured moving on its own (ADR-0007). `open_failures`
    travels beside the figure so a partly-known MTTR cannot be read as a whole one.
    """
    printer = await a_printer(db_session)
    await summarised(db_session, printer, HOUR)
    await a_failure(desk, printer, at=HOUR, restored_at=HOUR + timedelta(minutes=30))
    await a_failure(desk, printer, at=HOUR + timedelta(hours=1))

    row = row_for(await report(db_session, fleet), printer)

    assert row.failures == 2
    assert row.closed_failures == 1
    assert row.open_failures == 1
    # Thirty, not fifteen: the open one is absent from the numerator *and* from the
    # count it is divided by.
    assert row.mttr_minutes == Decimal("30.0")


async def test_a_machine_whose_failures_are_all_open_has_no_repair_time(
    db_session: AsyncSession, fleet: FleetService, desk: ServiceDesk
) -> None:
    """Nothing repaired is no figure — never a zero-minute repair."""
    printer = await a_printer(db_session)
    await a_failure(desk, printer)

    row = row_for(await report(db_session, fleet), printer)

    assert row.mttr_minutes is None
    assert row.open_failures == 1


# ------------------------------------------------------------------- the funnel


async def test_a_failure_whose_cause_nobody_named_is_uncategorised_and_not_other(
    db_session: AsyncSession, fleet: FleetService, desk: ServiceDesk
) -> None:
    """«Причины отказов» counts named causes; the unnamed ones are counted beside it.

    Folding a `NULL` cause into `other` would put "we do not know" and "we looked,
    and it was something else" in one bar — and every failure the sweep opens starts
    unnamed, so that bar would mostly be the driver's silence.

    Two machines rather than two failures on one, because `uq_printer_failures_open`
    allows a machine only one unrestored failure at a time — the constraint
    `test_service_failures.py` exists to prove.
    """
    named = await a_printer(db_session, name="A-named")
    unnamed = await a_printer(db_session, name="B-unnamed")
    await a_failure(desk, named, at=HOUR, cause=FailureCause.OTHER)
    await a_failure(desk, unnamed, at=HOUR + timedelta(minutes=10), cause=None)

    answer = await report(db_session, fleet)

    assert [(bar.cause, bar.failures) for bar in answer.causes] == [(FailureCause.OTHER, 1)]
    assert answer.uncategorised == 1


# ---------------------------------------------------------------------- the roster


async def test_every_registered_printer_gets_a_row_even_with_no_rollups(
    db_session: AsyncSession, fleet: FleetService
) -> None:
    """A machine missing from the summary must not go missing from the table.

    Dropping it would shrink the roster quietly, which is the same flattery as a
    fabricated denominator wearing different clothes: the farm looks better the more
    of itself it stops reporting. `printers_reporting` beside `printers_listed` is
    how a reader can see how much of the table is nulls.
    """
    watched = await a_printer(db_session, name="A-watched")
    await a_printer(db_session, name="B-unwatched")
    await summarised(db_session, watched, HOUR)

    answer = await report(db_session, fleet)

    assert answer.printers_listed == 2
    assert answer.printers_reporting == 1
    assert len(answer.rows) == 2


async def test_a_retired_machine_keeps_the_window_it_broke_in(
    db_session: AsyncSession, fleet: FleetService, desk: ServiceDesk
) -> None:
    """Retiring a printer must not improve last month.

    Inactive machines are not listed wholesale — a farm on its third generation of
    hardware would otherwise show a table mostly made of null rows about printers
    that left. But one with evidence *in this window* stays, because dropping it
    would quietly delete a failure from the period it happened in.
    """
    retired = await a_printer(db_session, name="Z-retired", is_active=False)
    await a_failure(desk, retired)

    answer = await report(db_session, fleet)

    assert [row.printer_id for row in answer.rows] == [retired]
    assert answer.rows[0].failures == 1


async def test_a_retired_machine_with_nothing_in_the_window_is_not_listed(
    db_session: AsyncSession, fleet: FleetService
) -> None:
    """The other half of the rule above, so "keep the evidence" cannot become "keep
    everything"."""
    await a_printer(db_session, name="Z-retired", is_active=False)

    assert (await report(db_session, fleet)).rows == []
