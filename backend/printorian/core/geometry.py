"""The size of a part as it will actually be printed.

One rule, in one place, because it was being got wrong in two: **the bounding box
stored on an order line is the box of the *unscaled* mesh.**

`catalog.assets._mesh_dict` and `api/routers/_pricing_spec` both write
`analysis.bounding_box` verbatim, while `estimator.estimate(analysis, ...,
scale=scale)` applies the scale only to volume, mass and time. Nothing writes a
scaled box down anywhere, so every reader that wants the size of the thing the
farm is about to make has to apply the scale itself — and both readers had
forgotten to. A 100 mm mesh ordered at scale 3 is a 300 mm part, and
`workers/intake` was handing 100 mm to the planner (whose only geometric test is
`job.width_mm > printer.width_mm`) while `workers/packaging` was recommending a
carton for 100 mm.

Scaling here rather than at the writers is deliberate. `mesh` is the analysis *as
measured*, and a scaled number in it would be a measurement the farm never took —
the same rule ADR-0007 states about telemetry. The scale belongs to the line, not
to the geometry, so the multiplication belongs at the read.

`None` rather than zeros for geometry nobody measured: a zero box reads as "fits
every machine" and as "adds nothing to the parcel", and both of those are claims.
Each caller decides what to do about not knowing, and they decide differently —
the planner's job columns default to zero because a *manual* path has a person
looking at the bed, while `workers/plate_admission` refuses outright.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Box:
    """A part's extent in millimetres, at the size it was ordered."""

    x: Decimal
    y: Decimal
    z: Decimal


def scaled_box(mesh: Mapping[str, Any] | Any, scale: Decimal) -> Box | None:
    """The part's bounding box at ``scale``, or ``None`` when it was not measured.

    Every failure to read one answers ``None`` rather than raising: `mesh` is JSONB
    written by several releases of the analyser, and a line the farm cannot measure
    must not stop the pass that is reading it.
    """
    if not isinstance(mesh, Mapping):
        return None
    box = mesh.get("bounding_box_mm")
    if not isinstance(box, Mapping):
        return None
    try:
        return Box(
            x=Decimal(str(box["x"])) * scale,
            y=Decimal(str(box["y"])) * scale,
            z=Decimal(str(box["z"])) * scale,
        )
    except (KeyError, ArithmeticError, TypeError, ValueError):
        return None


__all__ = ["Box", "scaled_box"]
