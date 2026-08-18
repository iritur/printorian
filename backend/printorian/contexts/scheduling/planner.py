"""The planner: which job goes to which printer, and why.

A pure function (ARCHITECTURE §6). No database, no clock, no network — ``now`` is
an argument, so the same inputs always produce the same plan. That is what lets it
run against the virtual farm, be replayed from a fixture when someone disputes an
assignment, and be tested without hardware.

**Every job produces an audit record**, assigned or not. "Why did job #4127 go to
P1S-03?" has to be answerable from the database, and answering it means keeping
the machines that were rejected and the grounds — not just the winner. V1 could
not answer it because it never asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from printorian.contexts.fleet import JobRequirements, PrinterCapability, can_take
from printorian.contexts.scheduling.policies import (
    MATERIAL_REJECTIONS,
    SCORE_AMORTIZATION,
    SCORE_CAPABILITY_WASTE,
    SCORE_LOAD_BALANCE,
    SCORE_MATERIAL_HEADROOM,
    STRUCTURAL_REJECTIONS,
    WAIT_AWAITING_CAPACITY,
    WAIT_MATERIAL_NOT_LOADED,
    WAIT_NO_CAPABLE_PRINTER,
    SchedulingPolicy,
)
from printorian.core.colors import is_multicolor

# ------------------------------------------------------------------ inputs


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadyJob:
    """A job whose plate is prepared and which is waiting for a machine."""

    job_id: str
    order_id: str
    requirements: JobRequirements
    estimated_minutes: Decimal
    #: When the customer was promised it. ``None`` means no deadline pressure.
    due_at: datetime | None = None
    #: Higher runs sooner among jobs with equal deadline pressure.
    priority: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class SchedulablePrinter:
    """A machine as the planner sees it: what it can do, and when it is next free."""

    capability: PrinterCapability
    #: ``None`` when the machine is free now. Otherwise when it expects to finish.
    free_at: datetime | None = None
    amortization_per_hour: Decimal = Decimal(0)
    #: Work already planned onto this machine, for load balancing.
    queued_minutes: Decimal = Decimal(0)

    @property
    def printer_id(self) -> str:
        return self.capability.printer_id


# ----------------------------------------------------------------- outputs


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoreComponent:
    """One term of a printer's score. Kept so a decision can be re-read later."""

    code: str
    #: The raw penalty in ``[0, 1]`` before weighting.
    value: Decimal
    weight: Decimal

    @property
    def weighted(self) -> Decimal:
        return self.value * self.weight


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateVerdict:
    """What the planner concluded about one printer for one job."""

    printer_id: str
    eligible: bool
    #: Every reason it was refused, not just the first.
    reasons: tuple[str, ...] = ()
    components: tuple[ScoreComponent, ...] = ()

    @property
    def score(self) -> Decimal:
        return sum((c.weighted for c in self.components), Decimal(0))


@dataclass(frozen=True, slots=True, kw_only=True)
class AssignmentDecision:
    """The audit record: every candidate considered, and how it fared."""

    job_id: str
    candidates: tuple[CandidateVerdict, ...]
    chosen_printer_id: str | None = None

    @property
    def rejected(self) -> tuple[CandidateVerdict, ...]:
        return tuple(c for c in self.candidates if not c.eligible)


@dataclass(frozen=True, slots=True, kw_only=True)
class Assignment:
    job_id: str
    order_id: str
    printer_id: str
    score: Decimal
    starts_at: datetime
    decision: AssignmentDecision


@dataclass(frozen=True, slots=True, kw_only=True)
class WaitListEntry:
    """A job nothing can take yet, and an honest account of why.

    ``predicted_start`` is ``None`` whenever the wait does not depend on time
    alone. A job needing filament nobody has mounted, or a plate no machine on the
    farm can physically print, has no start time the system can know — and naming
    one anyway is how a customer is told a comfortable lie (ADR-0007's reasoning,
    applied to the queue instead of the drivers).
    """

    job_id: str
    order_id: str
    reason: str
    predicted_start: datetime | None = None
    #: Distinct grounds across every machine, for the cabinet and the floor.
    blocking_reasons: tuple[str, ...] = ()
    decision: AssignmentDecision | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Plan:
    """The outcome of one planning pass."""

    assignments: tuple[Assignment, ...] = ()
    wait_list: tuple[WaitListEntry, ...] = ()
    decisions: tuple[AssignmentDecision, ...] = field(default=())


# ------------------------------------------------------------------ scoring


def _capability_waste_cost(printer: SchedulablePrinter, job: ReadyJob) -> Decimal:
    """Penalise spending a flexible machine on a job that does not need it.

    A multi-material printer given a single-colour job is unavailable for the
    four-colour job that arrives an hour later, and nothing else on the farm can
    take that one. Scarce capability is worth protecting when an ordinary machine
    would do.
    """
    if not printer.capability.supports_multi_material:
        return Decimal(0)
    # A plate of two identical slots is single-colour work, and protecting scarce
    # capacity from it is exactly the point. See `core.colors`.
    return Decimal(0) if is_multicolor(job.requirements.colors) else Decimal(1)


def _material_headroom_cost(
    printer: SchedulablePrinter, job: ReadyJob, policy: SchedulingPolicy
) -> Decimal:
    """Penalise a spool that only just covers the job.

    Eligibility guarantees there is *enough* filament, which is not the same as
    enough to be comfortable: a plate that runs out at 90% wastes the material and
    every hour already spent on it. Cost falls to zero once the spool holds
    ``comfortable_headroom`` times what the job needs.
    """
    required = job.requirements.grams_required
    if required <= 0 or policy.comfortable_headroom <= 1:
        return Decimal(0)

    wanted = job.requirements.material_type.casefold()
    available = [
        grams
        for material, _colour, grams in printer.capability.loaded
        if material.casefold() == wanted
    ]
    if not available:
        # Unreachable for an eligible printer; a neutral answer beats a divide.
        return Decimal(0)

    ratio = max(available) / required
    if ratio >= policy.comfortable_headroom:
        return Decimal(0)
    # 1 when the spool exactly covers the job, falling to 0 at comfortable headroom.
    span = policy.comfortable_headroom - Decimal(1)
    return max(Decimal(0), min(Decimal(1), (policy.comfortable_headroom - ratio) / span))


def _amortization_cost(printer: SchedulablePrinter, policy: SchedulingPolicy) -> Decimal:
    """Ordinary work should land on ordinary machines.

    Capped at 1 so an unusually expensive printer cannot swamp every other term
    and turn the score into a single-factor decision.
    """
    if policy.expensive_per_hour <= 0:
        return Decimal(0)
    return min(Decimal(1), printer.amortization_per_hour / policy.expensive_per_hour)


def _load_cost(printer: SchedulablePrinter, policy: SchedulingPolicy) -> Decimal:
    if policy.load_horizon_minutes <= 0:
        return Decimal(0)
    return min(Decimal(1), printer.queued_minutes / policy.load_horizon_minutes)


def _score(
    printer: SchedulablePrinter, job: ReadyJob, policy: SchedulingPolicy
) -> tuple[ScoreComponent, ...]:
    return (
        ScoreComponent(
            code=SCORE_CAPABILITY_WASTE,
            value=_capability_waste_cost(printer, job),
            weight=policy.weight_capability_waste,
        ),
        ScoreComponent(
            code=SCORE_MATERIAL_HEADROOM,
            value=_material_headroom_cost(printer, job, policy),
            weight=policy.weight_material_headroom,
        ),
        ScoreComponent(
            code=SCORE_AMORTIZATION,
            value=_amortization_cost(printer, policy),
            weight=policy.weight_amortization,
        ),
        ScoreComponent(
            code=SCORE_LOAD_BALANCE,
            value=_load_cost(printer, policy),
            weight=policy.weight_load_balance,
        ),
    )


# ----------------------------------------------------------------- ordering


def _urgency_key(job: ReadyJob, now: datetime, policy: SchedulingPolicy) -> tuple[int, int, str]:
    """Due-date risk first, then priority, then job id.

    The job id is in the key on purpose: without a final deterministic tie-break,
    two jobs with identical urgency could swap places between runs and the same
    inputs would produce different plans — which makes an audit record impossible
    to reproduce.
    """
    if job.due_at is None:
        bucket = 2
    elif job.due_at <= now + timedelta(hours=float(policy.due_soon_hours)):
        bucket = 0
    else:
        bucket = 1
    return (bucket, -job.priority, job.job_id)


# -------------------------------------------------------------------- plan


def plan(
    jobs: list[ReadyJob],
    printers: list[SchedulablePrinter],
    now: datetime,
    policy: SchedulingPolicy | None = None,
) -> Plan:
    """Assign what can run now; wait-list the rest with a reason.

    Greedy and single-pass: jobs are taken in urgency order and each claims the
    cheapest machine still free in this round. A machine that has been given work
    is not offered again, so one pass cannot double-book a printer.
    """
    resolved = policy or SchedulingPolicy()
    assignments: list[Assignment] = []
    wait_list: list[WaitListEntry] = []
    decisions: list[AssignmentDecision] = []

    claimed: set[str] = set()

    for job in sorted(jobs, key=lambda candidate: _urgency_key(candidate, now, resolved)):
        candidates: list[CandidateVerdict] = []

        for printer in sorted(printers, key=lambda p: p.printer_id):
            if printer.printer_id in claimed:
                # Already given work in this pass. Recorded as a rejection so the
                # audit trail shows it was considered rather than overlooked.
                candidates.append(
                    CandidateVerdict(
                        printer_id=printer.printer_id,
                        eligible=False,
                        reasons=("reject.claimed_this_pass",),
                    )
                )
                continue

            verdict = can_take(printer.capability, job.requirements)
            candidates.append(
                CandidateVerdict(
                    printer_id=printer.printer_id,
                    eligible=verdict.eligible,
                    reasons=verdict.reasons,
                    components=_score(printer, job, resolved) if verdict.eligible else (),
                )
            )

        eligible = [c for c in candidates if c.eligible]
        if eligible:
            # Cheapest wins; printer id breaks ties so the plan is reproducible.
            best = min(eligible, key=lambda c: (c.score, c.printer_id))
            decision = AssignmentDecision(
                job_id=job.job_id,
                candidates=tuple(candidates),
                chosen_printer_id=best.printer_id,
            )
            assignments.append(
                Assignment(
                    job_id=job.job_id,
                    order_id=job.order_id,
                    printer_id=best.printer_id,
                    score=best.score,
                    starts_at=now,
                    decision=decision,
                )
            )
            decisions.append(decision)
            claimed.add(best.printer_id)
            continue

        decision = AssignmentDecision(job_id=job.job_id, candidates=tuple(candidates))
        decisions.append(decision)
        wait_list.append(_wait_list_entry(job, printers, candidates, now))

    return Plan(
        assignments=tuple(assignments),
        wait_list=tuple(wait_list),
        decisions=tuple(decisions),
    )


def _wait_list_entry(
    job: ReadyJob,
    printers: list[SchedulablePrinter],
    candidates: list[CandidateVerdict],
    now: datetime,
) -> WaitListEntry:
    """Classify the wait, and predict a start only when time alone will fix it."""
    by_id = {printer.printer_id: printer for printer in printers}
    grounds = {reason for c in candidates for reason in c.reasons}

    # A machine that could take this job if it were free and loaded — i.e. one
    # whose only objections are transient or about filament.
    workable = [
        c.printer_id
        for c in candidates
        if not (set(c.reasons) & STRUCTURAL_REJECTIONS) and c.printer_id in by_id
    ]

    if not workable:
        # Nothing on the farm can print this as configured. No amount of waiting
        # changes that, so no start time is offered and a human is needed.
        return WaitListEntry(
            job_id=job.job_id,
            order_id=job.order_id,
            reason=WAIT_NO_CAPABLE_PRINTER,
            blocking_reasons=tuple(sorted(grounds & STRUCTURAL_REJECTIONS))
            or tuple(sorted(grounds)),
        )

    blocked_on_material = [c.printer_id for c in candidates if set(c.reasons) & MATERIAL_REJECTIONS]
    only_material = len(blocked_on_material) == len(workable)
    if only_material:
        # Waiting on a person with a spool. The farm cannot know when that is.
        return WaitListEntry(
            job_id=job.job_id,
            order_id=job.order_id,
            reason=WAIT_MATERIAL_NOT_LOADED,
            blocking_reasons=tuple(sorted(grounds & MATERIAL_REJECTIONS)),
        )

    # Genuinely a capacity queue: the soonest a workable machine comes free.
    # A machine with no known finish time cannot contribute a prediction, so it
    # is left out rather than treated as free.
    frees = [by_id[printer_id].free_at for printer_id in workable]
    known = sorted(moment for moment in frees if moment is not None)
    predicted = known[0] if known else None
    return WaitListEntry(
        job_id=job.job_id,
        order_id=job.order_id,
        reason=WAIT_AWAITING_CAPACITY,
        predicted_start=max(predicted, now) if predicted else None,
        blocking_reasons=tuple(sorted(grounds)),
    )
