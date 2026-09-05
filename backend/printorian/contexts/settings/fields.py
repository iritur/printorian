"""The vocabulary a setting is described in: what kind it is, and where it lives.

Split down out of `sections.py`, at the seam that file's own docstring names —
the difference between the *words* a field is declared with and the *list* of
fields declared with them. `sections.py` was at 399 lines against a hard 400-line
gate, so the next parameter added would have failed the build, and the choice was
this seam or a cut where the counter happened to trip.

It is the lower half deliberately, not the upper: `sections.py` imports from here
and nothing here imports back, so adding a field cannot introduce a cycle. The
three names are re-exported from `sections.py` as well, because
`tools/check_context_isolation.py` holds other contexts to importing the package
`__init__` and only the package `__init__` — moving where a name is *defined* must
not move where it can be *imported from*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from printorian.core.config import Settings as CoreSettings


class Kind(StrEnum):
    """How a value is parsed, stored and drawn.

    Two facts in one word, and they have to stay one word: the console picks its
    control from `kind` and `catalogue._PARSERS` picks its parser from the same
    `kind`, so a value that draws as a table and parses as a string is not a state
    the pair can reach.

    `TABLE` is for the structured values — the volume ladder, the customer tiers,
    the postprocessing operations — whose editors differ enough from a scalar input
    that they get their own row type rather than a number in a box. Which table is
    settled by the *key*, in `catalogue._parse_table`: a second `Kind` per table
    would put the same fact in two places and let them drift.
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


def config_default(name: str) -> Any:
    """A config default, read off the pydantic field rather than by instantiating.

    Instantiating `CoreSettings` reads the environment, and the catalogue must
    answer "what does the code ship" with the environment's voice out of the room —
    otherwise a developer's `.env` becomes the default the screen offers the farm.
    """
    return CoreSettings.model_fields[name].default


__all__ = ["FieldSpec", "Kind", "Section", "config_default"]
