"""Post-production — an operator's shift.

Public interface. The defining rule: **the norm is a gauge, not a stick.** Every
task carries the time it should take, per step, visible before the step starts —
and every figure the shop is judged on is a recorded fact, never something a
person can award or withhold.
"""

from printorian.contexts.postproduction.board import (
    BOARD_LIMIT,
    COLUMNS,
    STATS_DAYS,
    board_columns,
    operation_stats,
    output_by_day,
    shift_kpi,
)
from printorian.contexts.postproduction.catalogue import InstructionCatalogue
from printorian.contexts.postproduction.events import (
    TaskCompleted,
    TaskRaised,
    TaskReturned,
    TaskStatusChanged,
)
from printorian.contexts.postproduction.models import (
    Consumable,
    InstructionStep,
    Operation,
    Task,
    TaskStep,
)
from printorian.contexts.postproduction.policies import (
    CURES,
    TRANSITIONS,
    OperationKind,
    TaskStatus,
    Urgency,
    assert_transition,
    can_transition,
    norm_minutes,
    pace_percent,
    urgency_for,
)
from printorian.contexts.postproduction.schemas import (
    Badge,
    Board,
    Column,
    CompleteStep,
    ConsumableView,
    CreateOperation,
    CreateStep,
    CreateTask,
    OperationStat,
    ReportDefect,
    Scorecard,
    ShiftKpi,
    StepView,
    TaskView,
)
from printorian.contexts.postproduction.scorecard import scorecards
from printorian.contexts.postproduction.service import PostProductionService

__all__ = [
    "BOARD_LIMIT",
    "COLUMNS",
    "CURES",
    "STATS_DAYS",
    "TRANSITIONS",
    "Badge",
    "Board",
    "Column",
    "CompleteStep",
    "Consumable",
    "ConsumableView",
    "CreateOperation",
    "CreateStep",
    "CreateTask",
    "InstructionCatalogue",
    "InstructionStep",
    "Operation",
    "OperationKind",
    "OperationStat",
    "PostProductionService",
    "ReportDefect",
    "Scorecard",
    "ShiftKpi",
    "StepView",
    "Task",
    "TaskCompleted",
    "TaskRaised",
    "TaskReturned",
    "TaskStatus",
    "TaskStatusChanged",
    "TaskStep",
    "TaskView",
    "Urgency",
    "assert_transition",
    "board_columns",
    "can_transition",
    "norm_minutes",
    "operation_stats",
    "output_by_day",
    "pace_percent",
    "scorecards",
    "shift_kpi",
    "urgency_for",
]
