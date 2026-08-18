"""Card previews: a line drawing derived from the mesh.

The kit is specific about what a catalogue card shows — *"inline SVG line drawings
on graph paper rather than photos: an engineering drawing is honest about a part
that does not exist yet, and it survives both themes"*. This produces that drawing
from the real geometry, so a card shows the part it is selling rather than a
generic cube.

**Why not render the 3D view in every card.** A WebGL context per card, twenty-four
cards to a page, and a browser that starts dropping the oldest context at about
sixteen — the first cards would silently go blank while scrolling. A projected
path costs nothing to draw, survives both themes because it is stroked with a
token, and needs no canvas at all. The 3D view stays where it is worth its cost:
the detail popup, one at a time.

Computed once, at the point a catalogue entry is created, and stored on the row.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from printorian.contexts.catalog.mesh import _parse

#: The card's drawing surface, matching the kit's `viewBox` on every model card.
VIEW_W = 200.0
VIEW_H = 150.0
PADDING = 14.0

#: Below this angle between adjacent faces, an edge is tessellation rather than a
#: feature — the seams across a curved surface, which a drawing should not show.
FEATURE_ANGLE_DEG = 24.0

#: Ceiling on emitted segments. A drawing with thousands of lines is a grey smear
#: at card size, and the JSON to carry it dwarfs the row it sits on. Past this the
#: feature angle is raised until it fits, which drops tessellation rather than
#: truncating the shape — a half-drawn outline would read as a broken part.
MAX_SEGMENTS = 420

#: Where escalation stops.
#:
#: Raising the angle only thins edges *between two faces*. An open mesh is mostly
#: boundary edges, which are always drawn because they are the edge of the surface,
#: so no threshold reduces them. When that happens the drawing is abandoned and the
#: card falls back to its placeholder — honest about being unable to draw the part
#: legibly, rather than shipping twenty kilobytes of grey smear.
MAX_FEATURE_ANGLE_DEG = 80.0

#: How the part is turned before projecting. A standard isometric-ish view: rotate
#: about Z, then tip forward, so a prismatic part shows three faces rather than
#: presenting as a flat rectangle.
_YAW_DEG = 35.0
_PITCH_DEG = 28.0

#: An edge shared by exactly this many faces is an ordinary interior edge; one
#: face means a boundary, and more means the mesh is non-manifold there.
_INTERIOR = 2


def _rotation() -> NDArray[np.float64]:
    yaw, pitch = np.radians(_YAW_DEG), np.radians(_PITCH_DEG)
    around_z = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    around_x = np.array(
        [[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]]
    )
    rotation: NDArray[np.float64] = around_x @ around_z
    return rotation


def _edges_with_faces(
    triangles: NDArray[np.float64],
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int64]]:
    """Unique undirected edges, and which faces meet along each.

    Vertices are quantised before matching: two triangles that share a corner
    rarely agree bit-for-bit after a modelling package has written them out, and
    an edge that fails to match its twin looks like a boundary.
    """
    quantised = np.round(triangles.reshape(-1, 3), 4)
    _, vertex_ids = np.unique(quantised, axis=0, return_inverse=True)
    corners = vertex_ids.reshape(-1, 3)

    pairs = np.concatenate([corners[:, [0, 1]], corners[:, [1, 2]], corners[:, [2, 0]]], axis=0)
    pairs = np.sort(pairs, axis=1)
    faces = np.tile(np.arange(len(corners)), 3)

    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    return pairs[order], faces[order], vertex_ids


def _normals(triangles: NDArray[np.float64]) -> NDArray[np.float64]:
    spans = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(spans, axis=1, keepdims=True)
    # A degenerate triangle has no direction; give it one rather than dividing by
    # zero and poisoning every comparison it takes part in with NaN.
    return np.divide(spans, np.where(lengths == 0, 1.0, lengths))


def outline(data: bytes, *, feature_angle_deg: float = FEATURE_ANGLE_DEG) -> dict[str, Any]:
    """An isometric line drawing of an STL, as SVG path data.

    Returns the shape stored on `CatalogModel.preview`, or an empty dict when the
    mesh cannot be read — the card then falls back to its schematic placeholder,
    which is honest about knowing nothing rather than drawing a wrong part.
    """
    try:
        triangles = _parse(data)
    except Exception:  # pragma: no cover - a malformed upload is not this module's problem
        return {}
    if triangles.shape[0] == 0:
        return {}

    pairs, faces, vertex_ids = _edges_with_faces(triangles)
    normals = _normals(triangles)

    rotated = triangles.reshape(-1, 3) @ _rotation().T
    # Orthographic: drop depth, and flip Y because SVG counts downward.
    flat = np.column_stack([rotated[:, 0], -rotated[:, 2]])
    unique_points = np.zeros((int(vertex_ids.max()) + 1, 2))
    unique_points[vertex_ids] = flat

    # Which faces point away from the viewer, for silhouette detection.
    view = _rotation().T @ np.array([0.0, -1.0, 0.0])
    facing = normals @ view

    keep: list[int] = []
    threshold = np.cos(np.radians(feature_angle_deg))
    index = 0
    while index < len(pairs):
        run = 1
        while (
            index + run < len(pairs)
            and pairs[index + run][0] == pairs[index][0]
            and pairs[index + run][1] == pairs[index][1]
        ):
            run += 1

        if run == 1:
            # An open edge. Always drawn: it is the boundary of the surface.
            keep.append(index)
        elif run == _INTERIOR:
            left, right = faces[index], faces[index + 1]
            silhouette = (facing[left] > 0) != (facing[right] > 0)
            sharp = float(normals[left] @ normals[right]) < threshold
            if silhouette or sharp:
                keep.append(index)
        # A run above two is non-manifold; drawing it adds noise, not information.
        index += run

    if not keep:
        return {}

    # Too dense to read at card size? Redraw with only the sharper features.
    if len(keep) > MAX_SEGMENTS:
        if feature_angle_deg < MAX_FEATURE_ANGLE_DEG:
            return outline(data, feature_angle_deg=feature_angle_deg + 18.0)
        # Escalation exhausted — see `MAX_FEATURE_ANGLE_DEG`.
        return {}

    segments = unique_points[pairs[keep]]
    return {
        "view": "iso",
        "paths": _to_paths(segments),
        # Recorded so a later change to the projection can be spotted rather than
        # silently mixing two generations of drawing in one grid.
        "generator": "iso-outline/1",
    }


def _to_paths(segments: NDArray[np.float64]) -> list[str]:
    """Fit the projected segments to the card's viewBox and format them."""
    points = segments.reshape(-1, 2)
    low, high = points.min(axis=0), points.max(axis=0)
    span = np.where((high - low) == 0, 1.0, high - low)

    scale = float(min((VIEW_W - 2 * PADDING) / span[0], (VIEW_H - 2 * PADDING) / span[1]))
    centred = (points - (low + high) / 2) * scale + np.array([VIEW_W / 2, VIEW_H / 2])
    fitted = centred.reshape(-1, 2, 2)

    # One `M…L…` per segment, rounded to a tenth of a unit: the drawing is 200
    # units wide, so more precision than that is bytes nobody can see.
    return [f"M{a[0]:.1f} {a[1]:.1f}L{b[0]:.1f} {b[1]:.1f}" for a, b in fitted]
