"""STL parsing and mesh analysis.

Takes bytes and returns geometry. No filesystem, no database — the caller decides
where the bytes came from, which keeps this testable with a cube built in memory.

What the farm actually needs from a customer upload, before anyone slices it:

* **volume** — drives the material estimate, and therefore the price
* **bounding box** — decides which printers can physically fit the job
* **watertight** — a mesh with holes has no well-defined volume, so any price
  quoted from it is a guess presented as a fact
* **thin walls / tiny features** — cheap warnings that prevent a print destined to
  fail, caught before the customer pays rather than after
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

from printorian.core.errors import ValidationError
from printorian.core.units import BoundingBox, Length, Volume

_BINARY_HEADER_BYTES = 80
_BINARY_COUNT_BYTES = 4
_BINARY_TRIANGLE_BYTES = 50
_MAX_TRIANGLES = 5_000_000

#: Coordinates are snapped to this grid before edges are matched, so that vertices
#: written by different slicers as 1.0000001 and 0.9999999 count as the same point.
_WELD_DECIMALS = 4

#: Above this, the manifold check is skipped rather than spending minutes on it.
_MANIFOLD_CHECK_LIMIT = 400_000

#: A closed surface shares every edge between exactly two faces.
_FACES_PER_EDGE = 2


class MeshQuality(StrEnum):
    """How much the geometry can be trusted."""

    CLEAN = "clean"
    WARNING = "warning"
    UNUSABLE = "unusable"


@dataclass(frozen=True, slots=True, kw_only=True)
class MeshWarning:
    """A machine-readable finding. The client renders the text (ADR-0012)."""

    code: str
    details: dict[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class MeshAnalysis:
    """Everything measured from an uploaded mesh."""

    triangle_count: int
    volume: Volume
    bounding_box: BoundingBox
    surface_area_mm2: Decimal
    is_watertight: bool
    warnings: tuple[MeshWarning, ...] = ()

    @property
    def quality(self) -> MeshQuality:
        if not self.is_watertight:
            return MeshQuality.UNUSABLE
        return MeshQuality.WARNING if self.warnings else MeshQuality.CLEAN

    @property
    def is_priceable(self) -> bool:
        """A non-watertight mesh has no defined volume, so it cannot be priced."""
        return self.is_watertight and self.volume.cubic_centimetres > 0


def analyse_stl(data: bytes, *, check_manifold: bool = True) -> MeshAnalysis:
    """Parse an STL (binary or ASCII) and measure it."""
    triangles = _parse(data)
    if triangles.shape[0] == 0:
        raise ValidationError("error.catalog.mesh_empty")

    volume_mm3 = _signed_volume(triangles)
    area_mm2 = _surface_area(triangles)
    box = _bounding_box(triangles)

    watertight = True
    warnings: list[MeshWarning] = []
    if check_manifold:
        if triangles.shape[0] <= _MANIFOLD_CHECK_LIMIT:
            watertight = _is_watertight(triangles)
        else:
            warnings.append(
                MeshWarning(
                    code="warning.catalog.manifold_check_skipped",
                    details={"triangles": str(triangles.shape[0])},
                )
            )

    if not watertight:
        warnings.append(MeshWarning(code="warning.catalog.not_watertight", details={}))

    # A shell whose volume is tiny next to its surface area is mostly thin walls.
    # The ratio is a proxy for average thickness: 2*V/A approximates it for a shell.
    if volume_mm3 > 0 and area_mm2 > 0:
        approx_thickness = (2 * volume_mm3) / area_mm2
        if approx_thickness < Decimal("0.8"):
            warnings.append(
                MeshWarning(
                    code="warning.catalog.thin_walls",
                    details={"approx_thickness_mm": f"{approx_thickness:.2f}"},
                )
            )

    return MeshAnalysis(
        triangle_count=int(triangles.shape[0]),
        volume=Volume(volume_mm3 / Decimal(1000)),  # mm3 -> cm3
        bounding_box=box,
        surface_area_mm2=area_mm2,
        is_watertight=watertight,
        warnings=tuple(warnings),
    )


# ------------------------------------------------------------------ parsing


def _parse(data: bytes) -> NDArray[np.float64]:
    """Return an (n, 3, 3) array of triangle vertices in millimetres."""
    if len(data) < _BINARY_HEADER_BYTES + _BINARY_COUNT_BYTES:
        raise ValidationError("error.catalog.mesh_truncated", size=len(data))

    return _parse_ascii(data) if _looks_ascii(data) else _parse_binary(data)


def _looks_ascii(data: bytes) -> bool:
    """Decide by declared size, not by the leading word.

    Plenty of binary STLs start with "solid" because the exporter left the header
    blank, so trusting that prefix is a classic way to mis-parse a file.
    """
    count = struct.unpack_from("<I", data, _BINARY_HEADER_BYTES)[0]
    expected = _BINARY_HEADER_BYTES + _BINARY_COUNT_BYTES + count * _BINARY_TRIANGLE_BYTES
    if len(data) == expected:
        return False
    return data[:5].lower() == b"solid"


def _parse_binary(data: bytes) -> NDArray[np.float64]:
    count = int(struct.unpack_from("<I", data, _BINARY_HEADER_BYTES)[0])
    if count > _MAX_TRIANGLES:
        raise ValidationError("error.catalog.mesh_too_large", triangles=count)

    expected = _BINARY_HEADER_BYTES + _BINARY_COUNT_BYTES + count * _BINARY_TRIANGLE_BYTES
    if len(data) < expected:
        raise ValidationError("error.catalog.mesh_truncated", expected=expected, actual=len(data))

    record = np.dtype([("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attr", "<u2")])
    parsed = np.frombuffer(
        data, dtype=record, count=count, offset=_BINARY_HEADER_BYTES + _BINARY_COUNT_BYTES
    )
    return np.asarray(parsed["vertices"], dtype=np.float64)


_VERTEX = re.compile(rb"vertex\s+(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)", re.IGNORECASE)


def _parse_ascii(data: bytes) -> NDArray[np.float64]:
    values = _VERTEX.findall(data)
    if not values:
        raise ValidationError("error.catalog.mesh_no_vertices")
    if len(values) % 3 != 0:
        raise ValidationError("error.catalog.mesh_incomplete_facet", vertices=len(values))
    if len(values) // 3 > _MAX_TRIANGLES:
        raise ValidationError("error.catalog.mesh_too_large", triangles=len(values) // 3)

    flat = np.array(values, dtype=np.float64)
    return flat.reshape(-1, 3, 3)


# ----------------------------------------------------------------- geometry


def _signed_volume(triangles: NDArray[np.float64]) -> Decimal:
    """Volume by summing signed tetrahedra from the origin (divergence theorem).

    Correct for any closed surface regardless of where the origin sits. The
    absolute value is taken because a mesh with inverted normals would otherwise
    report a negative volume.
    """
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    total = float(np.abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))
    return Decimal(str(round(total, 6)))


def _surface_area(triangles: NDArray[np.float64]) -> Decimal:
    edge1 = triangles[:, 1] - triangles[:, 0]
    edge2 = triangles[:, 2] - triangles[:, 0]
    total = float(np.linalg.norm(np.cross(edge1, edge2), axis=1).sum() / 2.0)
    return Decimal(str(round(total, 6)))


def _bounding_box(triangles: NDArray[np.float64]) -> BoundingBox:
    points = triangles.reshape(-1, 3)
    extent = points.max(axis=0) - points.min(axis=0)
    return BoundingBox(
        x=Length(Decimal(str(round(float(extent[0]), 4)))),
        y=Length(Decimal(str(round(float(extent[1]), 4)))),
        z=Length(Decimal(str(round(float(extent[2]), 4)))),
    )


def _is_watertight(triangles: NDArray[np.float64]) -> bool:
    """True when every edge is shared by exactly two faces.

    Vertices are welded onto a grid first: STL stores raw coordinates with no
    shared index, so bit-identical corners are the exception rather than the rule.
    """
    welded = np.round(triangles, _WELD_DECIMALS).reshape(-1, 9)
    points = welded.reshape(-1, 3)
    _, indices = np.unique(points, axis=0, return_inverse=True)
    faces = indices.reshape(-1, 3)

    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], axis=0)
    edges.sort(axis=1)  # undirected
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return bool(np.all(counts == _FACES_PER_EDGE))
