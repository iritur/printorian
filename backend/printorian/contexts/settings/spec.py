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

from printorian.contexts.pricing import (
    FINISH_CATALOGUE,
    LOYALTY_LADDER,
    CustomerTier,
    FinishOption,
)
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


def default_finishes() -> tuple[FinishOption, ...]:
    """The postprocessing operations as the pricing engine already prices them.

    Derived from `FINISH_CATALOGUE` exactly as `default_tiers` is derived from
    `LOYALTY_LADDER`, and **not** transcribed from the design kit, which shows
    different numbers: `design/settings.html` draws primed at 0.7 h and painted at
    1.6 h where `pricing/finishes.py` charges 0.6 and 1.5. Typing the kit's figures
    in here would have quietly repriced every quote on the day this merged, under a
    commit message about making a table editable — the farm never asked for a rise,
    and the default is what it is running today.

    `extra_days` rides along even though no editor draws it — and, checked rather
    than assumed, nothing reads it either: it is declared as the calendar days a
    finish adds, and `promised_hours` takes policy, minutes, quantity and rush.
    Dropping it here would still be wrong. A default that quietly loses a column is
    how a field stays dead: the day a consumer arrives, «Окраска» has to still say
    2. Making it *editable* is a different question and not this slice's.
    """
    return tuple(FINISH_CATALOGUE.values())


__all__ = ["FieldSpec", "Kind", "Section", "cfg", "default_finishes", "default_tiers"]
