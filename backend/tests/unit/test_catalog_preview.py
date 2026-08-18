"""The card's line drawing, projected from the mesh.

The failure this guards against is quiet: every card falling back to the same
schematic placeholder, which looks deliberate rather than broken. A grid where
every part is drawn identically is worse than one with no drawings at all, because
it claims to show you the thing it is selling.
"""

from __future__ import annotations

import struct

import pytest

from printorian.contexts.catalog.preview import MAX_SEGMENTS, VIEW_H, VIEW_W, outline

Vec = tuple[float, float, float]
Tri = tuple[Vec, Vec, Vec]


def binary_stl(triangles: list[Tri]) -> bytes:
    out = bytearray(b"test".ljust(80, b"\0")) + struct.pack("<I", len(triangles))
    for tri in triangles:
        out += struct.pack("<3f", 0.0, 0.0, 0.0)
        for vertex in tri:
            out += struct.pack("<3f", *vertex)
        out += struct.pack("<H", 0)
    return bytes(out)


def cube(size: float = 10.0) -> list[Tri]:
    s = size
    a, b, c, d = (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0)
    e, f, g, h = (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)
    return [
        (a, c, b),
        (a, d, c),
        (e, f, g),
        (e, g, h),
        (a, b, f),
        (a, f, e),
        (b, c, g),
        (b, g, f),
        (c, d, h),
        (c, h, g),
        (d, a, e),
        (d, e, h),
    ]


def _coordinates(drawing: dict[str, object]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for path in drawing["paths"]:  # type: ignore[index]
        head, tail = str(path).split("L")
        x0, y0 = head[1:].split(" ")
        x1, y1 = tail.split(" ")
        points.append((float(x0), float(y0)))
        points.append((float(x1), float(y1)))
    return points


def test_a_cube_is_drawn_as_its_visible_edges() -> None:
    """Twelve edges, of which the isometric view shows nine.

    Three are hidden behind the solid. There is no hidden-line removal here, so
    what is asserted is that the drawing is *edges of this cube* rather than a
    count of exactly nine.
    """
    drawing = outline(binary_stl(cube()))

    assert drawing["view"] == "iso"
    assert 9 <= len(drawing["paths"]) <= 12


def test_the_drawing_fits_the_cards_viewbox() -> None:
    """A path outside the box is a drawing the card silently crops."""
    drawing = outline(binary_stl(cube(size=250.0)))

    for x, y in _coordinates(drawing):
        assert 0 <= x <= VIEW_W, x
        assert 0 <= y <= VIEW_H, y


def test_scale_does_not_change_the_drawing() -> None:
    """A 5 mm part and a 500 mm one of the same shape draw identically.

    The card has one size; the drawing is normalised into it. Without this a clip
    would be a dot beside a tray, which is a size *facet*, not a picture.
    """
    small = outline(binary_stl(cube(size=5.0)))
    large = outline(binary_stl(cube(size=500.0)))

    assert small["paths"] == large["paths"]


def test_two_different_shapes_draw_differently() -> None:
    """The bug this module exists to fix: every card showing the same picture."""
    flat = [
        ((0, 0, 0), (90, 0, 0), (90, 60, 0)),
        ((0, 0, 0), (90, 60, 0), (0, 60, 0)),
    ]
    assert outline(binary_stl(cube()))["paths"] != outline(binary_stl(flat))["paths"]


def test_a_dense_closed_mesh_is_thinned_by_raising_the_feature_angle() -> None:
    """Tessellation is dropped; the shape is not truncated.

    A faceted cylinder is mostly seams between nearly-coplanar faces. Those go, and
    what survives is the silhouette and the two rims — a drawing, not a smear.
    """
    import math

    sides, radius, height = 360, 20.0, 30.0
    ring = [
        (radius * math.cos(2 * math.pi * i / sides), radius * math.sin(2 * math.pi * i / sides))
        for i in range(sides)
    ]
    triangles: list[Tri] = []
    for i in range(sides):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % sides]
        triangles += [
            ((x0, y0, 0), (x1, y1, 0), (x1, y1, height)),
            ((x0, y0, 0), (x1, y1, height), (x0, y0, height)),
            ((0, 0, 0), (x1, y1, 0), (x0, y0, 0)),  # bottom cap
            ((0, 0, height), (x0, y0, height), (x1, y1, height)),  # top cap
        ]

    drawing = outline(binary_stl(triangles))

    assert drawing != {}, "a closed mesh should still yield a drawing"
    assert len(drawing["paths"]) <= MAX_SEGMENTS


def test_a_mesh_that_cannot_be_thinned_yields_no_drawing() -> None:
    """An open sheet is nearly all boundary edges, and those are never dropped.

    No feature angle reduces them, so the drawing is abandoned rather than shipped
    as an unreadable smear. The card falls back to its placeholder.
    """
    triangles: list[Tri] = []
    for index in range(400):
        x0, x1 = index * 0.5, (index + 1) * 0.5
        triangles += [
            ((x0, 0, 0), (x1, 0, 0), (x1, 10, 0)),
            ((x0, 0, 0), (x1, 10, 0), (x0, 10, 0)),
        ]

    assert outline(binary_stl(triangles)) == {}


@pytest.mark.parametrize("data", [b"", b"not an stl at all", b"solid\nendsolid\n"])
def test_unreadable_geometry_yields_no_drawing(data: bytes) -> None:
    """Empty, so the card falls back to its placeholder.

    Honest about knowing nothing, rather than drawing a part that is not this one.
    """
    assert outline(data) == {}
