"""Scheduling: which job runs on which machine, and why.

A pure planner (ARCHITECTURE §6). Eligibility is not restated here — it is
imported from ``fleet``, because there must be exactly one answer to "can this
machine take this job".
"""

from printorian.contexts.scheduling.planner import (
    Assignment,
    AssignmentDecision,
    CandidateVerdict,
    Plan,
    ReadyJob,
    SchedulablePrinter,
    ScoreComponent,
    WaitListEntry,
    plan,
)
from printorian.contexts.scheduling.policies import (
    MATERIAL_REJECTIONS,
    SCORE_AMORTIZATION,
    SCORE_CAPABILITY_WASTE,
    SCORE_LOAD_BALANCE,
    SCORE_MATERIAL_HEADROOM,
    STRUCTURAL_REJECTIONS,
    TRANSIENT_REJECTIONS,
    WAIT_AWAITING_CAPACITY,
    WAIT_MATERIAL_NOT_LOADED,
    WAIT_NO_CAPABLE_PRINTER,
    SchedulingPolicy,
)

__all__ = [
    "MATERIAL_REJECTIONS",
    "SCORE_AMORTIZATION",
    "SCORE_CAPABILITY_WASTE",
    "SCORE_LOAD_BALANCE",
    "SCORE_MATERIAL_HEADROOM",
    "STRUCTURAL_REJECTIONS",
    "TRANSIENT_REJECTIONS",
    "WAIT_AWAITING_CAPACITY",
    "WAIT_MATERIAL_NOT_LOADED",
    "WAIT_NO_CAPABLE_PRINTER",
    "Assignment",
    "AssignmentDecision",
    "CandidateVerdict",
    "Plan",
    "ReadyJob",
    "SchedulablePrinter",
    "SchedulingPolicy",
    "ScoreComponent",
    "WaitListEntry",
    "plan",
]
