"""Manifold solids from 2D profiles, for the catalogue seed.

Why not just stack boxes: two boxes that share a face are *not* a manifold union.
The coincident faces stay in the mesh and the shared edges end up owned by four
triangles instead of two, so `analyse_stl` correctly reports the result as not
watertight — and a non-watertight model cannot be priced, which would make the
seeded shop window a catalogue of unorderable parts.

Extruding a closed simple polygon avoids the problem by construction: every edge
of the resulting prism belongs to exactly two triangles.
"""

from __future__ import annotations

Point = tuple[float, float]
Vec = tuple[float, float, float]
Tri = tuple[Vec, Vec, Vec]


#: A polygon needs at least three vertices; below that there is no ear to clip.
_MIN_POLYGON = 3

#: Ear clipping terminates in O(n²) for a simple polygon. The bound only exists
#: so a malformed profile fails fast instead of hanging the seed script.
_MAX_ITERATIONS = 10_000


def _area(polygon: list[Point]) -> float:
    """Twice the signed area. Positive is counter-clockwise."""
    total = 0.0
    for index, (x0, y0) in enumerate(polygon):
        x1, y1 = polygon[(index + 1) % len(polygon)]
        total += x0 * y1 - x1 * y0
    return total


def _inside(point: Point, a: Point, b: Point, c: Point) -> bool:
    """Whether `point` is strictly inside triangle `abc`."""

    def side(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])

    d1, d2, d3 = side(point, a, b), side(point, b, c), side(point, c, a)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def triangulate(polygon: list[Point]) -> list[tuple[int, int, int]]:
    """Ear-clip a simple polygon into triangle indices.

    Handles the concave profiles here — an L and a U — which a triangle fan would
    get wrong by emitting triangles that fall outside the shape.
    """
    points = list(polygon)
    if _area(points) < 0:
        points.reverse()
    indices = list(range(len(points)))
    out: list[tuple[int, int, int]] = []

    guard = 0
    while len(indices) >= _MIN_POLYGON and guard < _MAX_ITERATIONS:
        guard += 1
        for position in range(len(indices)):
            i0 = indices[(position - 1) % len(indices)]
            i1 = indices[position]
            i2 = indices[(position + 1) % len(indices)]
            a, b, c = points[i0], points[i1], points[i2]
            # Convex corner?
            if (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]) <= 0:
                continue
            # No other vertex inside it?
            if any(
                _inside(points[other], a, b, c) for other in indices if other not in (i0, i1, i2)
            ):
                continue
            out.append((i0, i1, i2))
            indices.pop(position)
            break
        else:  # pragma: no cover - only reachable for a self-intersecting profile
            break

    return out


def extrude(profile: list[Point], height: float) -> list[Tri]:
    """A closed prism from a profile in the XY plane, extruded along +Z.

    Winding is outward throughout: `analyse_stl` measures volume as a signed
    tetrahedron sum, so an inward-wound solid reports negative volume and reads as
    unpriceable.
    """
    points = list(profile)
    if _area(points) < 0:
        points.reverse()

    caps = triangulate(points)
    bottom = [(x, y, 0.0) for x, y in points]
    top = [(x, y, height) for x, y in points]

    tris: list[Tri] = []
    for i0, i1, i2 in caps:
        # Bottom faces down, so its winding is reversed relative to the top.
        tris.append((bottom[i0], bottom[i2], bottom[i1]))
        tris.append((top[i0], top[i1], top[i2]))

    count = len(points)
    for index in range(count):
        nxt = (index + 1) % count
        tris.append((bottom[index], bottom[nxt], top[nxt]))
        tris.append((bottom[index], top[nxt], top[index]))
    return tris
