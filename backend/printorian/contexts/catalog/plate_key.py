"""The identity of a prepared plate.

ADR-0006 keys a `PreparedPlate` on
`(model_asset, scale, material_spec, printer_profile, plate_layout_hash)`. This
turns that tuple into one content-addressed string, the same technique the pricing
context uses for `RateSnapshot`: the key *is* the inputs, so two plates collide if
and only if they were sliced from the same thing.

Pure and deterministic — no clock, no randomness, no I/O. A key computed today and
one computed next year from the same inputs must match, or the cache silently
stops hitting and every repeat order goes back through an engineer.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

#: Bumped when the *meaning* of the key changes — a new field, or a different
#: normalisation. Old plates then miss rather than being matched by a key that no
#: longer means what it did.
KEY_VERSION = "v1"


def _normalise(value: str) -> str:
    """Case- and whitespace-insensitive, so "PLA " and "pla" are one material."""
    return " ".join(value.split()).casefold()


def _normalise_scale(scale: Decimal) -> str:
    """A fixed exponent, so 1, 1.0 and 1.000 are the same scale.

    Without this, `Decimal("1")` and `Decimal("1.00")` produce different keys and
    the same configuration would be sliced twice.
    """
    return str(scale.quantize(Decimal("0.0001")))


def plate_key(
    *,
    model_hash: str,
    scale: Decimal,
    material_code: str,
    printer_profile: str,
    layout_hash: str = "",
) -> str:
    """The cache key for one sliced configuration.

    ``model_hash`` identifies the geometry itself — a digest of the uploaded mesh,
    not a filename. Two customers uploading byte-identical cubes under different
    names share a plate; the same filename holding different geometry does not.
    """
    parts = (
        KEY_VERSION,
        model_hash.strip().casefold(),
        _normalise_scale(scale),
        _normalise(material_code),
        _normalise(printer_profile),
        _normalise(layout_hash),
    )
    # A separator that cannot appear in the normalised parts, so
    # ("ab", "c") and ("a", "bc") cannot produce the same digest.
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
