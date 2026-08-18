"""Mesh analysis, checked against shapes whose true volume is known exactly.

A cube's volume is not a matter of opinion, which makes it the right thing to test
a volume integrator with. If the price is built on this number, the number has to
be right.
"""

from __future__ import annotations

import struct
from decimal import Decimal

import pytest

from printorian.contexts.catalog import MeshQuality, analyse_stl
from printorian.core.errors import ValidationError

Triangle = tuple[tuple[float, float, float], ...]


def cube_triangles(size: float = 10.0, origin: float = 0.0) -> list[Triangle]:
    """A closed, correctly-wound axis-aligned cube: 12 triangles, volume size**3."""
    o = origin
    s = origin + size
    corners = {
        "000": (o, o, o),
        "100": (s, o, o),
        "110": (s, s, o),
        "010": (o, s, o),
        "001": (o, o, s),
        "101": (s, o, s),
        "111": (s, s, s),
        "011": (o, s, s),
    }
    faces = [
        ("000", "110", "100"),
        ("000", "010", "110"),  # bottom (-z)
        ("001", "101", "111"),
        ("001", "111", "011"),  # top (+z)
        ("000", "100", "101"),
        ("000", "101", "001"),  # front (-y)
        ("010", "011", "111"),
        ("010", "111", "110"),  # back (+y)
        ("000", "001", "011"),
        ("000", "011", "010"),  # left (-x)
        ("100", "110", "111"),
        ("100", "111", "101"),  # right (+x)
    ]
    return [tuple(corners[name] for name in face) for face in faces]


def to_binary_stl(triangles: list[Triangle]) -> bytes:
    out = bytearray(b"\0" * 80)
    out += struct.pack("<I", len(triangles))
    for triangle in triangles:
        out += struct.pack("<3f", 0.0, 0.0, 0.0)
        for vertex in triangle:
            out += struct.pack("<3f", *vertex)
        out += struct.pack("<H", 0)
    return bytes(out)


def to_ascii_stl(triangles: list[Triangle]) -> bytes:
    lines = ["solid test"]
    for triangle in triangles:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        lines.extend(f"      vertex {v[0]} {v[1]} {v[2]}" for v in triangle)
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid test")
    return "\n".join(lines).encode("ascii")


# ------------------------------------------------------------------ parsing


def test_binary_stl_volume_is_exact() -> None:
    analysis = analyse_stl(to_binary_stl(cube_triangles(10.0)))
    # 10mm cube = 1000 mm3 = 1 cm3
    assert analysis.volume.cubic_centimetres == pytest.approx(Decimal(1), abs=Decimal("0.001"))
    assert analysis.triangle_count == 12


def test_ascii_stl_parses_to_the_same_geometry() -> None:
    binary = analyse_stl(to_binary_stl(cube_triangles(10.0)))
    ascii_ = analyse_stl(to_ascii_stl(cube_triangles(10.0)))

    assert ascii_.triangle_count == binary.triangle_count
    assert ascii_.volume.cubic_centimetres == pytest.approx(
        binary.volume.cubic_centimetres, abs=Decimal("0.001")
    )


def test_binary_file_beginning_with_solid_is_not_mistaken_for_ascii() -> None:
    """A blank binary header often starts with "solid"; size decides, not the prefix."""
    data = bytearray(to_binary_stl(cube_triangles(10.0)))
    data[0:5] = b"solid"

    analysis = analyse_stl(bytes(data))
    assert analysis.triangle_count == 12


def test_volume_is_independent_of_position() -> None:
    """The divergence-theorem sum must not care where the origin sits."""
    at_origin = analyse_stl(to_binary_stl(cube_triangles(10.0, origin=0.0)))
    far_away = analyse_stl(to_binary_stl(cube_triangles(10.0, origin=250.0)))

    assert at_origin.volume.cubic_centimetres == pytest.approx(
        far_away.volume.cubic_centimetres, abs=Decimal("0.01")
    )


def test_volume_scales_with_the_cube_of_size() -> None:
    small = analyse_stl(to_binary_stl(cube_triangles(10.0)))
    large = analyse_stl(to_binary_stl(cube_triangles(20.0)))

    ratio = large.volume.cubic_centimetres / small.volume.cubic_centimetres
    assert ratio == pytest.approx(Decimal(8), abs=Decimal("0.01"))


def test_bounding_box_and_surface_area() -> None:
    analysis = analyse_stl(to_binary_stl(cube_triangles(10.0)))

    assert analysis.bounding_box.x.millimetres == pytest.approx(Decimal(10), abs=Decimal("0.01"))
    assert analysis.bounding_box.z.millimetres == pytest.approx(Decimal(10), abs=Decimal("0.01"))
    # A 10mm cube has six 100mm2 faces.
    assert analysis.surface_area_mm2 == pytest.approx(Decimal(600), abs=Decimal("0.1"))


# ------------------------------------------------------------- watertightness


def test_a_closed_cube_is_watertight_and_priceable() -> None:
    analysis = analyse_stl(to_binary_stl(cube_triangles()))

    assert analysis.is_watertight
    assert analysis.is_priceable
    assert analysis.quality is MeshQuality.CLEAN


def test_a_cube_missing_a_face_is_not_watertight_and_not_priceable() -> None:
    """A hole means the volume is undefined, so no honest price can come from it."""
    holed = cube_triangles()[:-2]
    analysis = analyse_stl(to_binary_stl(holed))

    assert not analysis.is_watertight
    assert not analysis.is_priceable
    assert analysis.quality is MeshQuality.UNUSABLE
    assert any(w.code == "warning.catalog.not_watertight" for w in analysis.warnings)


def test_vertices_are_welded_before_edges_are_matched() -> None:
    """Exporters write the same corner with tiny float differences; still watertight."""
    nudged: list[Triangle] = []
    for index, triangle in enumerate(cube_triangles()):
        if index == 0:
            nudged.append(tuple((x + 1e-7, y, z) for x, y, z in triangle))
        else:
            nudged.append(triangle)

    assert analyse_stl(to_binary_stl(nudged)).is_watertight


def test_inverted_normals_still_give_a_positive_volume() -> None:
    flipped = [(a, c, b) for a, b, c in cube_triangles()]
    analysis = analyse_stl(to_binary_stl(flipped))

    assert analysis.volume.cubic_centimetres > 0


# ------------------------------------------------------------------- guards


def test_empty_and_truncated_files_are_rejected() -> None:
    with pytest.raises(ValidationError):
        analyse_stl(b"")
    with pytest.raises(ValidationError) as excinfo:
        analyse_stl(to_binary_stl(cube_triangles())[:120])
    assert excinfo.value.code == "error.catalog.mesh_truncated"


def test_zero_triangle_file_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        analyse_stl(to_binary_stl([]))
    assert excinfo.value.code == "error.catalog.mesh_empty"


def test_ascii_with_an_incomplete_facet_is_rejected() -> None:
    text = to_ascii_stl(cube_triangles()).decode()
    truncated = text.rsplit("vertex", 1)[0].encode()

    with pytest.raises(ValidationError):
        analyse_stl(truncated)


# ------------------------------------------------- the configurator's cache


def test_the_same_model_is_analysed_once() -> None:
    """Every hover in the configurator re-uploads the same STL. Without this the
    server re-parses the whole mesh to reach an answer it already had, which is
    the dominant cost of a price preview."""
    from printorian.api.routers import pricing

    pricing._analysis_cache.clear()
    data = to_binary_stl(cube_triangles(10.0))

    calls = 0
    original = pricing.analyse_stl

    def counting(payload: bytes):
        nonlocal calls
        calls += 1
        return original(payload)

    pricing.analyse_stl = counting  # type: ignore[assignment]
    try:
        first = pricing._analyse_cached(data)
        second = pricing._analyse_cached(data)
    finally:
        pricing.analyse_stl = original  # type: ignore[assignment]

    assert calls == 1
    assert first is second


def test_a_different_model_is_never_served_from_the_cache() -> None:
    """Keyed on a digest of the content, so one customer's model can never be
    priced with another's geometry."""
    from printorian.api.routers import pricing

    pricing._analysis_cache.clear()
    small = pricing._analyse_cached(to_binary_stl(cube_triangles(10.0)))
    large = pricing._analyse_cached(to_binary_stl(cube_triangles(40.0)))

    assert small.volume.cubic_centimetres != large.volume.cubic_centimetres


def test_the_cache_does_not_grow_without_bound() -> None:
    """A busy farm must not accumulate every model ever quoted."""
    from printorian.api.routers import pricing

    pricing._analysis_cache.clear()
    for size in range(1, pricing._ANALYSIS_CACHE_SIZE + 6):
        pricing._analyse_cached(to_binary_stl(cube_triangles(float(size))))

    assert len(pricing._analysis_cache) <= pricing._ANALYSIS_CACHE_SIZE
