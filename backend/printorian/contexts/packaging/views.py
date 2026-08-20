"""Turning a stored parcel into the card and the popup the screen draws.

Its own module because two callers need it and must not each grow their own. The
board renders a hundred cards from one query and the service returns one after a
write; if the elapsed time on a card and the elapsed time in the popup behind it
were computed by different code they would disagree within a shift, and the first
person to notice would be an operator being told they are slower than they are.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from printorian.contexts.packaging.models import PackInstruction, PackTask, Tara
from printorian.contexts.packaging.policies import Dims, volumetric_grams
from printorian.contexts.packaging.schemas import PackStepView, PackView
from printorian.contexts.postproduction import pace_percent, urgency_for


def view_of(
    task: PackTask,
    *,
    now: datetime,
    suggested: Tara | None = None,
    chosen: Tara | None = None,
) -> PackView:
    """One card, from an eagerly loaded row.

    Shared by the service and the board so the popup and the card behind it can
    never disagree about elapsed time — which they would within a shift if each
    computed its own.
    """
    live = task.elapsed_minutes
    if task.running_since is not None:
        live += _minutes(now - task.running_since)
    to_cutoff = _minutes(task.cutoff_at - now) if task.cutoff_at is not None else None
    steps = [PackStepView.model_validate(step) for step in task.steps]
    remaining = sum((step.norm_minutes for step in steps if step.done_at is None), Decimal(0))
    dims = Dims(task.length_mm, task.width_mm, task.height_mm)

    return PackView(
        id=task.id,
        number=task.number,
        status=task.status,
        order_id=task.order_id,
        delivery_method=task.delivery_method,
        carrier_code=task.carrier_code,
        cutoff_at=task.cutoff_at,
        urgency=urgency_for(to_cutoff),
        minutes_to_cutoff=to_cutoff,
        items=task.items,
        estimated_grams=task.estimated_grams,
        length_mm=task.length_mm,
        width_mm=task.width_mm,
        height_mm=task.height_mm,
        volumetric_grams=volumetric_grams(dims),
        wrap_required=task.wrap_required,
        tara_id=task.tara_id,
        tara_name=chosen.name if chosen else "",
        recommended_tara_id=suggested.id if suggested else None,
        recommended_tara_name=suggested.name if suggested else "",
        weight_grams=task.weight_grams,
        packaging_cost=task.packaging_cost,
        norm_minutes=task.norm_minutes,
        elapsed_minutes=live.quantize(Decimal("0.01")),
        instruction_version=task.instruction_version,
        pace_percent=pace_percent(task.norm_minutes, live),
        projected_minutes=(live + remaining).quantize(Decimal("0.1")),
        operator_id=task.operator_id,
        started_at=task.started_at,
        finished_at=task.finished_at,
        shipped_at=task.shipped_at,
        hold_reason=task.hold_reason,
        discrepancy_code=task.discrepancy_code,
        discrepancy_note=task.discrepancy_note,
        discrepancy_at=task.discrepancy_at,
        steps=steps,
    )


def _minutes(delta: timedelta) -> Decimal:
    return Decimal(str(delta / timedelta(minutes=1))).quantize(Decimal("0.01"))


def total_norm(instruction: PackInstruction | None) -> Decimal:
    """The instruction's own total, which is the norm for one parcel.

    Not multiplied by the piece count, unlike a finishing task: packing ten
    brackets into one box is one pack. Where quantity does cost time it is inside
    a step's own norm — counting and wrapping — and that is the level the farm
    can actually measure.
    """
    if instruction is None:
        return Decimal(0)
    return sum((step.norm_minutes for step in instruction.steps), Decimal(0))


__all__ = ["total_norm", "view_of"]
