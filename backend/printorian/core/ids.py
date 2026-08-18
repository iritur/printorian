"""Entity identifiers.

UUIDv7 primary keys: globally unique like UUIDv4, but the leading 48 bits are a
millisecond timestamp, so keys sort by creation time. That gives Postgres good
B-tree locality on inserts and makes "recent rows" range-scannable — which a
telemetry-heavy system needs.

Python 3.13 has no ``uuid.uuid7``; this is a minimal RFC 9562 §5.7 implementation.
"""

from __future__ import annotations

import os
import time
from uuid import UUID

EntityId = UUID

_VERSION = 7
_VERSION_7 = 0x7000  # version nibble, positioned in the low 16 bits of the high word
_RFC_VARIANT = 0x8000  # variant "10", positioned in the high 16 bits of the low word


def new_id() -> EntityId:
    """Generate a time-ordered UUIDv7."""
    return uuid7(time.time_ns() // 1_000_000)


def uuid7(unix_ms: int) -> UUID:
    """Build a UUIDv7 for an explicit millisecond timestamp (testable)."""
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits
    rand_a = (rand >> 62) & 0x0FFF  # 12 bits
    rand_b = rand & 0x3FFF_FFFF_FFFF_FFFF  # 62 bits

    #  127..80 timestamp | 79..76 version | 75..64 rand_a | 63..62 variant | 61..0 rand_b
    high = ((unix_ms & 0xFFFF_FFFF_FFFF) << 16) | _VERSION_7 | rand_a
    low = (_RFC_VARIANT << 48) | rand_b
    return UUID(int=(high << 64) | low)


def timestamp_of(value: EntityId) -> int:
    """Extract the creation timestamp (unix milliseconds) from a UUIDv7."""
    if value.version != _VERSION:
        raise ValueError(f"not a UUIDv7: {value}")
    return value.int >> 80
