"""The plate cache — the thing that keeps human-gated slicing from scaling
linearly with orders (ADR-0006).

The key carries the weight: if it is unstable, every repeat order silently goes
back through an engineer and nobody notices except the engineer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import (
    PlateLibrary,
    PlateStatus,
    RecordPlate,
    plate_key,
)
from printorian.core.clock import FixedClock
from printorian.core.ids import new_id
from tests.conftest import ensure_user


@pytest.fixture
def library(db_session: AsyncSession, clock: FixedClock) -> PlateLibrary:
    return PlateLibrary(db_session, clock)


def a_plate(**overrides: object) -> RecordPlate:
    base: dict[str, object] = {
        "model_hash": "abc123",
        "model_name": "cube.stl",
        "scale": Decimal(1),
        "material_code": "pla-white",
        "printer_profile": "p1s-0.4-pla",
        "print_minutes": Decimal(72),
        "filament_grams": {"0": Decimal("17.3")},
        "filename": "cube.3mf",
        "slicer_name": "BambuStudio",
        "slicer_version": "1.9.5",
        "profile_version": "2026.1",
    }
    return RecordPlate(**{**base, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------------------- key


def test_the_same_configuration_gives_the_same_key() -> None:
    args = {
        "model_hash": "abc",
        "scale": Decimal(1),
        "material_code": "pla-white",
        "printer_profile": "p1s",
    }
    assert plate_key(**args) == plate_key(**args)  # type: ignore[arg-type]


def test_scale_is_normalised_so_one_and_one_point_zero_match() -> None:
    """Otherwise `Decimal("1")` and `Decimal("1.00")` slice the same thing twice."""
    base = {"model_hash": "abc", "material_code": "pla", "printer_profile": "p1s"}
    assert plate_key(scale=Decimal(1), **base) == plate_key(  # type: ignore[arg-type]
        scale=Decimal("1.000"),
        **base,  # type: ignore[arg-type]
    )


def test_material_case_and_spacing_do_not_split_the_cache() -> None:
    base = {"model_hash": "abc", "scale": Decimal(1), "printer_profile": "p1s"}
    assert plate_key(material_code="PLA White", **base) == plate_key(  # type: ignore[arg-type]
        material_code=" pla   white ",
        **base,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_hash", "different"),
        ("scale", Decimal(2)),
        ("material_code", "petg-clear"),
        ("printer_profile", "a1-0.4-pla"),
        ("layout_hash", "two-up"),
    ],
)
def test_every_part_of_the_key_changes_it(field: str, value: object) -> None:
    """Each is in ADR-0006's tuple because each changes what comes off the bed."""
    base: dict[str, object] = {
        "model_hash": "abc",
        "scale": Decimal(1),
        "material_code": "pla",
        "printer_profile": "p1s",
        "layout_hash": "",
    }
    assert plate_key(**base) != plate_key(**{**base, field: value})  # type: ignore[arg-type]


def test_the_separator_prevents_a_field_boundary_collision() -> None:
    """("ab","c") and ("a","bc") must not hash alike."""
    left = plate_key(model_hash="ab", scale=Decimal(1), material_code="c", printer_profile="p")
    right = plate_key(model_hash="a", scale=Decimal(1), material_code="bc", printer_profile="p")
    assert left != right


# --------------------------------------------------------------- library


async def test_a_recorded_plate_is_found_again(library: PlateLibrary) -> None:
    """This hit is the difference between automatic dispatch and an engineer."""
    await library.record(a_plate())

    found = await library.find(
        model_hash="abc123",
        scale=Decimal(1),
        material_code="pla-white",
        printer_profile="p1s-0.4-pla",
    )

    assert found is not None
    assert found.print_minutes == Decimal(72)


async def test_a_different_configuration_is_a_miss(library: PlateLibrary) -> None:
    await library.record(a_plate())

    assert (
        await library.find(
            model_hash="abc123",
            scale=Decimal(2),
            material_code="pla-white",
            printer_profile="p1s-0.4-pla",
        )
        is None
    )


async def test_re_slicing_replaces_the_truth_rather_than_adding_a_row(
    library: PlateLibrary,
) -> None:
    """Two rows for one configuration would make "which plate" a coin toss."""
    await library.record(a_plate())
    await library.record(a_plate(print_minutes=Decimal(80)))

    found = await library.find(
        model_hash="abc123",
        scale=Decimal(1),
        material_code="pla-white",
        printer_profile="p1s-0.4-pla",
    )
    assert found is not None
    assert found.print_minutes == Decimal(80)


async def test_re_slicing_clears_a_copy_count_the_new_slice_did_not_state(
    library: PlateLibrary,
) -> None:
    """The one field on this row that decides whether a later order attaches alone.

    `record` overwrites every field rather than merging, and the comment above
    `plate.copies` says that is deliberate — but nothing held it. The natural
    refactor ("do not clobber what the caller did not send") passes thirty-nine
    tests, and this is what it costs: a three-up plate is recorded with `copies=3`;
    the configuration is later re-sliced **one-up** and re-recorded through
    `POST /jobs/{id}/plate/file`, which sends no `copies` at all — that is what the
    console does today — so the minutes and grams become the one-up figures while
    `copies` stays 3. The next order for three then attaches a one-up plate,
    `attach_plate` writes a third of the work onto the job, the reprice divides
    that third by three and lands well inside ADR-0013's band, and the farm prints
    one, ships three short, and records an accurate estimate.

    Which is the defect `PreparedPlate.copies` exists to prevent, reintroduced
    through the single line that prevents it.
    """
    await library.record(a_plate(copies=3, print_minutes=Decimal(240)))
    await library.record(a_plate(print_minutes=Decimal(80)))

    found = await library.find(
        model_hash="abc123",
        scale=Decimal(1),
        material_code="pla-white",
        printer_profile="p1s-0.4-pla",
    )
    assert found is not None
    assert found.print_minutes == Decimal(80)
    # Not 3. A stale count beside fresh minutes is worse than no count at all:
    # `workers/plate_admission` refuses a NULL and would have trusted the 3.
    assert found.copies is None


async def test_provenance_is_kept(
    library: PlateLibrary, clock: FixedClock, db_session: AsyncSession
) -> None:
    """ADR-0006: a plate that cannot say who and what produced it cannot be
    invalidated with confidence when the profile moves."""
    who = new_id()
    # `prepared_plates.sliced_by` is a real foreign key.
    await ensure_user(db_session, who, email="engineer@example.test")
    plate = await library.record(a_plate(sliced_by=who))

    assert plate.sliced_by == who
    assert plate.sliced_at == clock.now()
    assert plate.slicer_version == "1.9.5"
    assert plate.profile_version == "2026.1"


async def test_grams_are_kept_per_slot(library: PlateLibrary) -> None:
    """A total would hide that one spool is nearly out while others are full."""
    plate = await library.record(
        a_plate(filament_grams={"0": Decimal("10.5"), "1": Decimal("4.25")})
    )

    assert plate.filament_grams == {"0": "10.5", "1": "4.25"}
    assert plate.total_grams == Decimal("14.75")


async def test_an_invalidated_plate_is_never_handed_out(library: PlateLibrary) -> None:
    """Printing from a profile somebody already rejected is worse than re-slicing."""
    plate = await library.record(a_plate())
    await library.invalidate(plate.id, status=PlateStatus.STALE)

    assert (
        await library.find(
            model_hash="abc123",
            scale=Decimal(1),
            material_code="pla-white",
            printer_profile="p1s-0.4-pla",
        )
        is None
    )


async def test_an_invalidated_plate_is_kept_not_deleted(library: PlateLibrary) -> None:
    """Jobs that already printed from it must stay explicable."""
    plate = await library.record(a_plate())
    await library.invalidate(plate.id, status=PlateStatus.REJECTED)

    assert (await library.get(plate.id)).status is PlateStatus.REJECTED


async def test_a_changed_profile_retires_every_plate_that_used_it(
    library: PlateLibrary,
) -> None:
    """One stroke, which is exactly why the profile is stored on each row."""
    await library.record(a_plate(model_hash="one"))
    await library.record(a_plate(model_hash="two"))
    await library.record(a_plate(model_hash="three", printer_profile="a1-0.4-pla"))

    retired = await library.invalidate_profile("p1s-0.4-pla")

    assert retired == 2
    assert (
        await library.find(
            model_hash="three",
            scale=Decimal(1),
            material_code="pla-white",
            printer_profile="a1-0.4-pla",
        )
        is not None
    )
