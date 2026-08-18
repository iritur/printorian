"""The planner.

Two things carry the weight here: that the plan is *reproducible* — same inputs,
same assignment, every time — and that a job which cannot run says honestly why,
without inventing a start time nobody can know.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from printorian.contexts.fleet import JobRequirements, PrinterCapability
from printorian.contexts.scheduling import (
    SCORE_CAPABILITY_WASTE,
    WAIT_AWAITING_CAPACITY,
    WAIT_MATERIAL_NOT_LOADED,
    WAIT_NO_CAPABLE_PRINTER,
    ReadyJob,
    SchedulablePrinter,
    SchedulingPolicy,
    plan,
)
from printorian.drivers import PrinterState

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def a_job(job_id: str = "j1", **overrides: object) -> ReadyJob:
    requirements = overrides.pop("requirements", None) or JobRequirements(
        width_mm=Decimal(100),
        depth_mm=Decimal(100),
        height_mm=Decimal(100),
        material_type="PLA",
        colors=("white",),
        grams_required=Decimal(50),
    )
    base: dict[str, object] = {
        "job_id": job_id,
        "order_id": f"o-{job_id}",
        "requirements": requirements,
        "estimated_minutes": Decimal(120),
    }
    return ReadyJob(**{**base, **overrides})  # type: ignore[arg-type]


def a_printer(printer_id: str = "p1", **overrides: object) -> SchedulablePrinter:
    loaded = overrides.pop("loaded", (("PLA", "white", Decimal(800)),))
    capability = PrinterCapability(
        printer_id=printer_id,
        state=overrides.pop("state", PrinterState.IDLE),  # type: ignore[arg-type]
        width_mm=overrides.pop("width_mm", Decimal(256)),  # type: ignore[arg-type]
        depth_mm=Decimal(256),
        height_mm=Decimal(256),
        nozzle_diameter_mm=Decimal("0.4"),
        supports_multi_material=overrides.pop("multi", True),  # type: ignore[arg-type]
        loaded=loaded,  # type: ignore[arg-type]
    )
    return SchedulablePrinter(capability=capability, **overrides)  # type: ignore[arg-type]


# ------------------------------------------------------------- assignment


def test_a_ready_job_goes_to_a_capable_printer() -> None:
    result = plan([a_job()], [a_printer()], NOW)

    assert len(result.assignments) == 1
    assert result.assignments[0].printer_id == "p1"
    assert not result.wait_list


def test_every_job_produces_an_audit_record() -> None:
    """Assigned or not — "why did this job go there" must be answerable later."""
    result = plan([a_job("j1"), a_job("j2")], [a_printer()], NOW)

    assert len(result.decisions) == 2
    assert {d.job_id for d in result.decisions} == {"j1", "j2"}


def test_the_decision_keeps_the_machines_that_lost_and_the_grounds() -> None:
    too_small = a_printer("p-small", width_mm=Decimal(50))
    result = plan([a_job()], [too_small, a_printer("p-ok")], NOW)

    decision = result.decisions[0]
    assert decision.chosen_printer_id == "p-ok"
    rejected = {c.printer_id: c.reasons for c in decision.rejected}
    assert "reject.build_volume" in rejected["p-small"]


def test_the_winning_score_is_kept_component_by_component() -> None:
    """A total alone cannot be argued with; the parts can."""
    result = plan([a_job()], [a_printer()], NOW)

    chosen = next(c for c in result.decisions[0].candidates if c.eligible)
    assert {component.code for component in chosen.components} >= {SCORE_CAPABILITY_WASTE}
    assert chosen.score == sum((c.weighted for c in chosen.components), Decimal(0))


def test_one_printer_cannot_be_given_two_jobs_in_a_pass() -> None:
    result = plan([a_job("j1"), a_job("j2")], [a_printer()], NOW)

    assert len(result.assignments) == 1
    assert len(result.wait_list) == 1


def test_the_second_job_records_why_the_machine_was_unavailable() -> None:
    """Not silently skipped: the audit trail shows it was considered."""
    result = plan([a_job("j1"), a_job("j2")], [a_printer()], NOW)

    waiting = next(d for d in result.decisions if d.job_id == result.wait_list[0].job_id)
    assert "reject.claimed_this_pass" in waiting.candidates[0].reasons


# --------------------------------------------------------------- ordering


def test_the_job_due_soonest_is_placed_first() -> None:
    urgent = a_job("j-urgent", due_at=NOW + timedelta(hours=2))
    relaxed = a_job("j-relaxed", due_at=NOW + timedelta(days=5))

    result = plan([relaxed, urgent], [a_printer()], NOW)

    assert result.assignments[0].job_id == "j-urgent"


def test_priority_breaks_ties_between_equally_urgent_jobs() -> None:
    low = a_job("j-low", due_at=NOW + timedelta(hours=2), priority=0)
    high = a_job("j-high", due_at=NOW + timedelta(hours=2), priority=5)

    result = plan([low, high], [a_printer()], NOW)

    assert result.assignments[0].job_id == "j-high"


def test_a_job_with_no_deadline_yields_to_one_with_a_deadline() -> None:
    result = plan(
        [a_job("j-none"), a_job("j-dated", due_at=NOW + timedelta(days=3))],
        [a_printer()],
        NOW,
    )

    assert result.assignments[0].job_id == "j-dated"


def test_the_same_inputs_always_produce_the_same_plan() -> None:
    """Without this an audit record cannot be reproduced, and two runs of the
    scheduler could disagree about a decision already acted on."""
    jobs = [a_job("j1"), a_job("j2"), a_job("j3")]
    printers = [a_printer("p1"), a_printer("p2"), a_printer("p3")]

    first = plan(jobs, printers, NOW)
    # Reversed inputs: order of arrival must not change the outcome.
    second = plan(list(reversed(jobs)), list(reversed(printers)), NOW)

    assert [(a.job_id, a.printer_id) for a in first.assignments] == [
        (a.job_id, a.printer_id) for a in second.assignments
    ]


# ---------------------------------------------------------------- scoring


def test_a_machine_without_the_material_is_refused_not_merely_penalised() -> None:
    """This is why "changeover cost" cannot be a soft term: a machine without the
    filament cannot print the job at all, so it never reaches the scorer."""
    loaded = a_printer("p-loaded", loaded=(("PLA", "white", Decimal(800)),))
    other = a_printer("p-other", loaded=(("PETG", "white", Decimal(800)),))

    result = plan([a_job()], [other, loaded], NOW)

    assert result.assignments[0].printer_id == "p-loaded"
    refused = next(c for c in result.decisions[0].candidates if c.printer_id == "p-other")
    assert "reject.material_not_loaded" in refused.reasons


def test_a_flexible_machine_is_kept_free_for_work_that_needs_it() -> None:
    """A four-colour AMS spent on a single-colour job is capacity the farm cannot
    use for the four-colour job that arrives later."""
    simple = a_printer("p-simple", multi=False)
    flexible = a_printer("p-flexible", multi=True)

    result = plan([a_job()], [flexible, simple], NOW)

    assert result.assignments[0].printer_id == "p-simple"


def test_a_multi_colour_job_may_use_the_flexible_machine() -> None:
    """The protection is against waste, not against using the machine at all."""
    two_colours = a_job(
        requirements=JobRequirements(
            width_mm=Decimal(100),
            depth_mm=Decimal(100),
            height_mm=Decimal(100),
            material_type="PLA",
            colors=("white", "black"),
            grams_required=Decimal(50),
        )
    )
    flexible = a_printer(
        "p-flexible",
        multi=True,
        loaded=(("PLA", "white", Decimal(800)), ("PLA", "black", Decimal(800))),
    )

    result = plan([two_colours], [flexible], NOW)

    assert result.assignments[0].printer_id == "p-flexible"


def test_a_spool_that_barely_covers_the_job_loses_to_a_full_one() -> None:
    """Eligibility says there is enough filament, not that there is enough to be
    comfortable. Running out at 90% wastes the plate and the hours behind it."""
    nearly_empty = a_printer("p-low", loaded=(("PLA", "white", Decimal(55)),))
    full = a_printer("p-full", loaded=(("PLA", "white", Decimal(900)),))

    result = plan([a_job()], [nearly_empty, full], NOW)

    assert result.assignments[0].printer_id == "p-full"


def test_the_cheaper_machine_wins_when_nothing_else_separates_them() -> None:
    cheap = a_printer("p-cheap", amortization_per_hour=Decimal(5))
    dear = a_printer("p-dear", amortization_per_hour=Decimal(50))

    result = plan([a_job()], [dear, cheap], NOW)

    assert result.assignments[0].printer_id == "p-cheap"


def test_work_spreads_rather_than_queueing_on_one_machine() -> None:
    busy = a_printer("p-busy", queued_minutes=Decimal(480))
    quiet = a_printer("p-quiet", queued_minutes=Decimal(0))

    result = plan([a_job()], [busy, quiet], NOW)

    assert result.assignments[0].printer_id == "p-quiet"


def test_weights_decide_which_consideration_wins() -> None:
    """The farm's priorities are configuration, not code (ADR-0010).

    The same two machines, and which one wins depends only on what the farm has
    said it cares about.
    """
    dear_but_plentiful = a_printer(
        "p-dear",
        amortization_per_hour=Decimal(50),
        loaded=(("PLA", "white", Decimal(900)),),
    )
    cheap_but_nearly_empty = a_printer(
        "p-cheap",
        amortization_per_hour=Decimal(1),
        loaded=(("PLA", "white", Decimal(55)),),
    )
    printers = [dear_but_plentiful, cheap_but_nearly_empty]

    filament_matters = SchedulingPolicy(
        weight_material_headroom=Decimal(20), weight_amortization=Decimal(1)
    )
    money_matters = SchedulingPolicy(
        weight_material_headroom=Decimal(0), weight_amortization=Decimal(20)
    )

    assert plan([a_job()], printers, NOW, filament_matters).assignments[0].printer_id == "p-dear"
    assert plan([a_job()], printers, NOW, money_matters).assignments[0].printer_id == "p-cheap"


# -------------------------------------------------------------- wait list


def test_a_job_no_machine_can_print_is_not_given_a_start_time() -> None:
    """The honest answer is "a person has to decide", not a date."""
    huge = a_job(
        requirements=JobRequirements(
            width_mm=Decimal(900),
            depth_mm=Decimal(900),
            height_mm=Decimal(900),
            material_type="PLA",
            grams_required=Decimal(50),
        )
    )

    result = plan([huge], [a_printer()], NOW)

    entry = result.wait_list[0]
    assert entry.reason == WAIT_NO_CAPABLE_PRINTER
    assert entry.predicted_start is None
    assert "reject.build_volume" in entry.blocking_reasons


def test_a_job_waiting_on_filament_is_not_given_a_start_time_either() -> None:
    """Nothing in the system knows when somebody will walk over with a spool."""
    wrong_material = a_printer("p1", loaded=(("PETG", "white", Decimal(800)),))

    result = plan([a_job()], [wrong_material], NOW)

    entry = result.wait_list[0]
    assert entry.reason == WAIT_MATERIAL_NOT_LOADED
    assert entry.predicted_start is None
    assert "reject.material_not_loaded" in entry.blocking_reasons


def test_a_job_queued_behind_a_running_print_gets_the_finish_time() -> None:
    finishes = NOW + timedelta(hours=3)
    printing = a_printer("p1", state=PrinterState.PRINTING, free_at=finishes)

    result = plan([a_job()], [printing], NOW)

    entry = result.wait_list[0]
    assert entry.reason == WAIT_AWAITING_CAPACITY
    assert entry.predicted_start == finishes


def test_a_busy_machine_with_no_known_finish_time_predicts_nothing() -> None:
    """A printer that has not reported an ETA cannot be used to promise one."""
    printing = a_printer("p1", state=PrinterState.PRINTING, free_at=None)

    result = plan([a_job()], [printing], NOW)

    entry = result.wait_list[0]
    assert entry.reason == WAIT_AWAITING_CAPACITY
    assert entry.predicted_start is None


def test_the_soonest_free_machine_sets_the_prediction() -> None:
    soon = a_printer("p-soon", state=PrinterState.PRINTING, free_at=NOW + timedelta(hours=1))
    later = a_printer("p-later", state=PrinterState.PRINTING, free_at=NOW + timedelta(hours=9))

    result = plan([a_job()], [later, soon], NOW)

    assert result.wait_list[0].predicted_start == NOW + timedelta(hours=1)


def test_a_prediction_is_never_in_the_past() -> None:
    """An overrunning print would otherwise promise a start that has been and gone."""
    overdue = a_printer("p1", state=PrinterState.PRINTING, free_at=NOW - timedelta(hours=2))

    result = plan([a_job()], [overdue], NOW)

    assert result.wait_list[0].predicted_start == NOW


def test_a_machine_in_maintenance_is_capacity_that_returns() -> None:
    """Distinct from a machine that could never do the job: this one comes back."""
    servicing = a_printer("p1", state=PrinterState.MAINTENANCE, free_at=NOW + timedelta(hours=4))

    result = plan([a_job()], [servicing], NOW)

    assert result.wait_list[0].reason == WAIT_AWAITING_CAPACITY


def test_nothing_is_scheduled_onto_an_empty_farm() -> None:
    result = plan([a_job()], [], NOW)

    assert result.wait_list[0].reason == WAIT_NO_CAPABLE_PRINTER
    assert result.wait_list[0].predicted_start is None


def test_an_empty_queue_plans_nothing_without_complaint() -> None:
    result = plan([], [a_printer()], NOW)

    assert result == type(result)()
