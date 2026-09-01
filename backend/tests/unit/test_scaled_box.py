"""The part's size at the size it was ordered, and the two readers of it.

`OrderLine.mesh` holds the box of the **unscaled** mesh: `_pricing_spec` writes
`analysis.bounding_box` verbatim while `estimator.estimate(..., scale=scale)`
applies the scale only to volume, mass and time. Nothing writes a scaled box down
anywhere, so both readers of that box have to apply the scale themselves — and
both had forgotten.

Two readers, two different failures, one multiplication:

* `workers/intake._dimensions` writes the job's width, depth and height, and
  `fleet.can_take`'s only geometric test is `job.width_mm > printer.width_mm`. A
  40 mm mesh at scale 3 is a 120 mm part judged as 40, so a printer that cannot
  hold it is eligible.
* `workers/packaging._line_dims` feeds the carton recommendation, which the
  module's own docstring says must never come out too small.

So the rule lives in `core.geometry.scaled_box` and is asserted here for the
function and for both callers. Pure — no database, no session: an `OrderLine`
built in memory is exactly what both readers take.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.ordering.models import OrderLine
from printorian.core.geometry import scaled_box
from printorian.workers.intake import _dimensions as job_dimensions
from printorian.workers.packaging import _line_dims

CUBE = {"bounding_box_mm": {"x": "40", "y": "30", "z": "20"}}


def a_line(*, scale: Decimal = Decimal(1), mesh: dict[str, object] | None = None) -> OrderLine:
    """A line as `place()` stores one. Defaults are set explicitly: a column
    default is applied by the database at flush, and nothing here flushes."""
    return OrderLine(
        model_name="cube.stl",
        material_code="pla-white",
        quantity=1,
        scale=scale,
        colors=["white"],
        finishes=[],
        mesh=dict(CUBE) if mesh is None else mesh,
        estimated_minutes=Decimal(120),
        estimated_grams=Decimal(50),
        line_total=Decimal(1000),
    )


def test_the_box_is_multiplied_by_the_scale() -> None:
    box = scaled_box(CUBE, Decimal(3))

    assert box is not None
    assert (box.x, box.y, box.z) == (Decimal(120), Decimal(90), Decimal(60))


def test_geometry_nobody_measured_is_none_rather_than_zero() -> None:
    """Zero is a claim — "fits every machine", "adds nothing to the parcel".

    Each caller decides what not knowing means, and they decide differently. The
    one thing none of them may do is confuse it with a measurement (ADR-0007).
    """
    assert scaled_box({}, Decimal(1)) is None
    assert scaled_box({"bounding_box_mm": None}, Decimal(1)) is None
    assert scaled_box({"bounding_box_mm": {"x": "1", "y": "2"}}, Decimal(1)) is None
    assert scaled_box({"bounding_box_mm": {"x": "wide", "y": "2", "z": "3"}}, Decimal(1)) is None
    assert scaled_box("not a mesh at all", Decimal(1)) is None


def test_the_job_carries_the_scaled_part() -> None:
    """`fleet.can_take` compares these three numbers to the printer's bed."""
    assert job_dimensions(a_line(scale=Decimal(3))) == {
        "width_mm": Decimal(120),
        "depth_mm": Decimal(90),
        "height_mm": Decimal(60),
    }


def test_the_carton_is_recommended_for_the_scaled_part() -> None:
    """A 3x part in a 1x box is the one failure a recommendation must not have."""
    dims = _line_dims(a_line(scale=Decimal(3)))

    assert dims is not None
    assert (dims.length_mm, dims.width_mm, dims.height_mm) == (
        Decimal(120),
        Decimal(90),
        Decimal(60),
    )


def test_an_unmeasured_line_still_contributes_nothing_to_the_parcel() -> None:
    """Unchanged, and load-bearing: a zero would shrink the batch and get a box
    recommended that the parcel does not fit in."""
    assert _line_dims(a_line(mesh={})) is None
