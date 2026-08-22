"""What a setting is called, what type it holds, and what it defaults to.

A key/value table cannot type-check anything — the database will happily store
``margin_percent = "thirty"`` — so the checking lives here, on the way in.

**The catalogue is derived, never hand-listed.** It is read off
``dataclasses.fields(RateSnapshot)``, which is the same argument ADR-0020 makes for
building `rates_to_dict` that way: a hand-listed set of keys silently omits the
next rate somebody adds, and a settings screen missing a rate is worse than one
that never had it, because it looks complete. Add a field to `RateSnapshot` and it
appears here, typed and defaulted, with no second place to remember.

That also fixes the default in exactly one place. `RateSnapshot`'s field default is
what the farm gets when no row exists, so "unset" and "set to the default" cannot
drift apart — they are the same number by construction rather than by upkeep.

## Scope

Only the pricing rates today. The other ~85 parameters of the kit's settings
screen live on `core.config.Settings` and reach the farm through a different path —
they are read at process start, not per request, so serving them from a table is a
change to *when* they are read as well as to where they come from, and that is its
own piece of work. `docs/DESIGN-KIT.md` §2.1 has the full inventory.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from printorian.contexts.pricing import RateSnapshot
from printorian.core.errors import NotFoundError, ValidationError

#: Prefix on every rate key, so a settings table shared with the other sections
#: later cannot collide — `pricing.margin_percent` and a future
#: `logistics.margin_percent` are different settings about different things.
RATE_PREFIX: Final = "pricing."


def _rate_fields() -> dict[str, Any]:
    """Every `RateSnapshot` field that is a plain scalar, by key."""
    found: dict[str, Any] = {}
    for field in fields(RateSnapshot):
        default = getattr(RateSnapshot(), field.name)
        # Discounts and tiers are nested structures with their own editing screens
        # in the kit (a ladder table, not a number in a box). Serving them through
        # the same scalar path would flatten a table into a string.
        if isinstance(default, Decimal | int | bool):
            found[f"{RATE_PREFIX}{field.name}"] = default
    return found


#: key -> default, computed once. `RateSnapshot` is frozen, so this cannot drift.
DEFAULTS: Final[dict[str, Any]] = _rate_fields()

KEYS: Final[tuple[str, ...]] = tuple(sorted(DEFAULTS))


def is_known(key: str) -> bool:
    return key in DEFAULTS


def default_for(key: str) -> Any:
    if key not in DEFAULTS:
        raise NotFoundError("error.settings.unknown_key", key=key)
    return DEFAULTS[key]


def to_json(value: Any) -> Any:
    """The shape stored in the JSONB column.

    Decimals go as **strings**. A JSON number is a float, and a float is not a
    price — the same rule `core.money` applies on the wire applies in the column.
    """
    return str(value) if isinstance(value, Decimal) else value


def from_json(key: str, raw: Any) -> Any:
    """Parse a stored or submitted value back into the type the field holds.

    Raises :class:`ValidationError` rather than coercing loosely: a settings screen
    that accepts `"30%"` and stores `30` has invented a number, and this one ends
    up in every quote the farm gives.
    """
    default = default_for(key)

    # bool first: it is an int subclass, and `isinstance(True, int)` is True.
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        raise ValidationError("error.settings.not_a_boolean", key=key)

    if isinstance(default, Decimal):
        if isinstance(raw, bool) or raw is None:
            raise ValidationError("error.settings.not_a_number", key=key)
        try:
            parsed = Decimal(str(raw))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("error.settings.not_a_number", key=key) from exc
        if not parsed.is_finite():
            raise ValidationError("error.settings.not_a_number", key=key)
        if parsed < 0:
            # Every rate here is a cost, a percentage or a count. None of them is
            # meaningfully negative, and a negative margin entered by mistake is a
            # farm quoting below cost until somebody notices.
            raise ValidationError("error.settings.negative", key=key)
        return parsed

    if isinstance(default, int):
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValidationError("error.settings.not_an_integer", key=key)
        if raw < 0:
            raise ValidationError("error.settings.negative", key=key)
        return raw

    raise ValidationError("error.settings.unsupported_type", key=key)


__all__ = [
    "DEFAULTS",
    "KEYS",
    "RATE_PREFIX",
    "default_for",
    "from_json",
    "is_known",
    "to_json",
]
