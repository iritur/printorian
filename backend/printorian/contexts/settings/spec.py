"""The vocabulary a setting is described in: its kind, its spec, its section.

Split out of `sections.py` when that file reached the 400-line gate. The seam is
the one the old file already drew in its own comments: this module is what a
setting *is*, `declared.py` is the list of the parameters themselves, and
`sections.py` assembles the two into the screen. Splitting where the counter
happened to trip instead would have put half the manual field list in a second
file and left no reader able to say which half.

`Kind`, `FieldSpec` and `Section` are re-exported from `sections.py`, which is
where every caller already imports them from — see the note on its `__all__`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from printorian.contexts.pricing import LOYALTY_LADDER, CustomerTier
from printorian.core.config import Settings as CoreSettings


class Kind(StrEnum):
    """How a value is parsed, stored and drawn.

    `TABLE` is for the structured values — the volume ladder, the customer tiers
    and the shipping zones — whose editors differ enough from a scalar input that
    they get their own row type rather than a number in a box.
    """

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    ENUM = "enum"
    SECRET = "secret"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One setting the screen draws: its key, where it lives, and what it holds.

    `default` is the code default — the value a reset returns to and an empty
    table prices/schedules with. `options` is non-empty only for enums.
    """

    key: str
    section: str
    kind: Kind
    default: Any
    options: tuple[str, ...] = ()
    #: The panel heading within a section (`pricing.labor`, `general.farm`, …).
    #: `None` means the section is one undivided panel.
    group: str | None = None


@dataclass(frozen=True, slots=True)
class Section:
    """A screen section, in the order the rail lists them."""

    id: str
    fields: tuple[str, ...]


#: A config default, read off the pydantic field rather than by instantiating
#: `CoreSettings` — the latter reads the environment, and the catalogue must
#: answer "what does the code ship" with the environment's voice out of the room.
def cfg(name: str) -> Any:
    return CoreSettings.model_fields[name].default


def default_tiers() -> tuple[CustomerTier, ...]:
    """The customer tiers as the loyalty ladder defines them, no margin override.

    The `from_spend` thresholds stay in `loyalty.py` — the kit's «Тарифы клиентов»
    table shows the discount and the margin override, not how a tier is *earned*,
    and earning is a loyalty mechanic, not a price-book setting.
    """
    return tuple(
        CustomerTier(code=step.code, discount_percent=step.discount_percent)
        for step in LOYALTY_LADDER
    )


__all__ = ["FieldSpec", "Kind", "Section", "cfg", "default_tiers"]
