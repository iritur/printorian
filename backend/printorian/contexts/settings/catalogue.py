"""What a setting is called, what type it holds, and what it defaults to.

A key/value table cannot type-check anything — the database will happily store
`margin_percent = "thirty"` — so the checking lives here, on the way in.

The field list itself lives in `sections.py`: it is the part that grows, and it
grows as data (a row per parameter), not as code. This module is the *rules* —
resolve a default, serialize a value for the column and the wire, and parse a
submitted value back into its type, refusing rather than coercing.

**The catalogue is derived, never hand-listed.** The pricing rates are read off
`dataclasses.fields(RateSnapshot)` and the scheduler weights off
`dataclasses.fields(SchedulingPolicy)`, for the same reason ADR-0020 argues for
`rates_to_dict`: a hand-listed set of keys silently omits the next rate somebody
adds, and a settings screen missing a rate is worse than one that never had it,
because it looks complete. The parameters no code consumes yet are the exception
that proves the rule — they are declared once, in `sections.py`, with the kit's
own value, and a consumer wires them to that name rather than to a second one.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Final

from printorian.contexts.pricing import CustomerTier, DiscountLadder, DiscountTier
from printorian.contexts.settings.sections import FIELDS, SECTIONS, Kind
from printorian.core.errors import NotFoundError, ValidationError

#: Prefix on every rate key, so a settings table shared with the other sections
#: cannot collide — `pricing.margin_percent` and `scheduling.margin_percent`
#: are different settings about different things, and only the prefix says so.
RATE_PREFIX: Final = "pricing."

#: key -> default, built from the field specs rather than a second list.
DEFAULTS: Final[dict[str, Any]] = {key: spec.default for key, spec in FIELDS.items()}

#: Sorted for stable iteration by callers that want a set; the screen itself reads
#: `FIELDS` in declaration order, which is the order the sections show them in.
KEYS: Final[tuple[str, ...]] = tuple(sorted(DEFAULTS))


def is_known(key: str) -> bool:
    return key in DEFAULTS


def kind_of(key: str) -> Kind:
    spec = FIELDS.get(key)
    if spec is None:
        raise NotFoundError("error.settings.unknown_key", key=key)
    return spec.kind


def default_for(key: str) -> Any:
    if key not in DEFAULTS:
        raise NotFoundError("error.settings.unknown_key", key=key)
    return DEFAULTS[key]


def to_json(value: Any) -> Any:
    """The shape stored in the JSONB column and sent over the wire.

    Decimals go as **strings**. A JSON number is a float, and a float is not a
    price — the same rule `core.money` applies on the wire applies in the column.
    A ladder becomes a list of `{min_quantity, percent}` dicts (percent as a
    string, for the same reason). Everything else is already JSON-shaped and
    passes through unchanged.
    """
    if isinstance(value, DiscountLadder):
        return [
            {"min_quantity": tier.min_quantity, "percent": str(tier.percent)}
            for tier in value.tiers
        ]
    if isinstance(value, tuple) and value and isinstance(value[0], CustomerTier):
        return [
            {
                "code": tier.code,
                "discount_percent": str(tier.discount_percent),
                "margin_percent_override": (
                    str(tier.margin_percent_override)
                    if tier.margin_percent_override is not None
                    else None
                ),
            }
            for tier in value
        ]
    return str(value) if isinstance(value, Decimal) else value


def _parse_decimal(key: str, raw: Any, options: tuple[str, ...]) -> Decimal:
    """Parse one decimal, refusing nonsense rather than coercing it."""
    if isinstance(raw, bool) or raw is None:
        raise ValidationError("error.settings.not_a_number", key=key)
    try:
        parsed = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("error.settings.not_a_number", key=key) from exc
    if not parsed.is_finite():
        raise ValidationError("error.settings.not_a_number", key=key)
    if parsed < 0:
        # Every decimal here is a cost, a percentage, a weight or a horizon.
        # None is meaningfully negative, and a negative margin entered by
        # mistake is a farm quoting below cost until somebody notices.
        raise ValidationError("error.settings.negative", key=key)
    return parsed


def _parse_enum(key: str, raw: Any, options: tuple[str, ...]) -> str:
    """Refuse anything outside the declared set, rather than storing a typo."""
    if not isinstance(raw, str) or raw not in options:
        raise ValidationError("error.settings.not_an_option", key=key)
    return raw


def _parse_secret(key: str, raw: Any, options: tuple[str, ...]) -> str:
    """A secret arrives as a non-empty string, is encrypted, and is never read back."""
    if not isinstance(raw, str) or not raw:
        raise ValidationError("error.settings.not_a_string", key=key)
    return raw


def _parse_ladder(key: str, raw: Any, options: tuple[str, ...]) -> DiscountLadder:
    """Parse a stored volume ladder — a list of `{min_quantity, percent}` dicts.

    `DiscountTier` and `DiscountLadder` validate as they are built: a tier below
    one unit, a percent outside `[0, 100)`, or a ladder that inverts all raise the
    pricing engine's own codes, so a bad ladder never reaches a quote. The only
    shape errors re-mapped here are the ones a settings *screen* should own —
    "not a list of dicts" is not a pricing rule.
    """
    if not isinstance(raw, list):
        raise ValidationError("error.settings.not_a_table", key=key)
    try:
        return DiscountLadder(
            tiers=tuple(
                DiscountTier(
                    min_quantity=int(item["min_quantity"]),
                    percent=Decimal(str(item["percent"])),
                )
                for item in raw
            )
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ValidationError("error.settings.not_a_table", key=key) from exc


def _parse_tiers(key: str, raw: Any, options: tuple[str, ...]) -> tuple[CustomerTier, ...]:
    """Parse the customer tiers — a list of `{code, discount_percent, margin_percent_override}`.

    `CustomerTier` itself carries no validation (unlike `DiscountTier`), so the
    discount is bounded here: a discount at or past 100% is a negative price, which
    no owner intends to set.
    """
    if not isinstance(raw, list):
        raise ValidationError("error.settings.not_a_table", key=key)
    try:
        tiers = tuple(
            CustomerTier(
                code=str(item["code"]),
                discount_percent=Decimal(str(item["discount_percent"])),
                margin_percent_override=(
                    Decimal(str(item["margin_percent_override"]))
                    if item.get("margin_percent_override") is not None
                    else None
                ),
            )
            for item in raw
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ValidationError("error.settings.not_a_table", key=key) from exc
    for tier in tiers:
        if not (Decimal(0) <= tier.discount_percent < Decimal(100)):
            raise ValidationError("error.settings.negative", key=key)
    return tiers


def _parse_table(key: str, raw: Any, options: tuple[str, ...]) -> Any:
    """The two table shapes, routed by key rather than by a second `Kind`."""
    if key == "pricing.discounts":
        return _parse_ladder(key, raw, options)
    if key == "pricing.tiers":
        return _parse_tiers(key, raw, options)
    raise ValidationError("error.settings.unsupported_type", key=key)


def _parse_string(key: str, raw: Any, options: tuple[str, ...]) -> str:
    if not isinstance(raw, str):
        raise ValidationError("error.settings.not_a_string", key=key)
    return raw


def _parse_boolean(key: str, raw: Any, options: tuple[str, ...]) -> bool:
    # bool first wherever int is an option: it is an int subclass, and
    # `isinstance(True, int)` is True.
    if isinstance(raw, bool):
        return raw
    raise ValidationError("error.settings.not_a_boolean", key=key)


def _parse_integer(key: str, raw: Any, options: tuple[str, ...]) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationError("error.settings.not_an_integer", key=key)
    if raw < 0:
        raise ValidationError("error.settings.negative", key=key)
    return raw


#: One parser per kind, keyed the same way the screen reads `field.kind`. A new
#: kind is one row here and one `Kind` member — never another branch in `from_json`.
_PARSERS: Final[dict[Kind, Any]] = {
    Kind.STRING: _parse_string,
    Kind.BOOLEAN: _parse_boolean,
    Kind.INTEGER: _parse_integer,
    Kind.DECIMAL: _parse_decimal,
    Kind.ENUM: _parse_enum,
    Kind.SECRET: _parse_secret,
    Kind.TABLE: _parse_table,
}


def from_json(key: str, raw: Any) -> Any:
    """Parse a stored or submitted value back into the type the field holds.

    Raises :class:`ValidationError` rather than coercing loosely: a settings
    screen that accepts `"30%"` and stores `30` has invented a number, and this
    one ends up in every quote the farm gives.
    """
    spec = FIELDS.get(key)
    if spec is None:
        raise NotFoundError("error.settings.unknown_key", key=key)
    parser = _PARSERS.get(spec.kind)
    if parser is None:
        # A kind with no parser: refuse rather than guess, so a new kind cannot
        # silently round-trip through the wrong branch.
        raise ValidationError("error.settings.unsupported_type", key=key)
    return parser(key, raw, spec.options)


__all__ = [
    "DEFAULTS",
    "FIELDS",
    "KEYS",
    "RATE_PREFIX",
    "SECTIONS",
    "Kind",
    "default_for",
    "from_json",
    "is_known",
    "kind_of",
    "to_json",
]
