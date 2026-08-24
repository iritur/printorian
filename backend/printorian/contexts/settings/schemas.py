"""What the settings API sends and accepts.

Values cross the wire in the shape the column stores them in — decimals as
strings — so the client renders exactly what the farm set, and a JSON float never
gets the chance to turn 6.50 into 6.5000000000000004.

A secret is the one value that never crosses the wire at all: it arrives, is
encrypted, and what comes back is only *whether one is set*. There is no "show me
the key" path, because there is no legitimate screen that shows it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from printorian.core.ids import EntityId


class SettingView(BaseModel):
    """One setting, as the screen draws it."""

    key: str
    #: The section the screen groups it under — the kit's heading, not the key's
    #: prefix. `pricing.packaging_per_unit` is shown under «Логистика».
    section: str
    #: What kind of control to draw, and how to parse a submitted value.
    kind: str
    #: What the farm is using: the override if there is one, else `default`.
    #: `None` for a secret — the value is never read back.
    value: Any
    #: What the code ships. The screen's «ПО УМОЛЧАНИЮ», and the value a reset
    #: returns to.
    default: Any
    #: Whether a row exists. Distinct from `value != default` on purpose: setting a
    #: rate to the number it already was is a decision somebody made, and the
    #: screen should show it as set rather than silently as untouched.
    is_overridden: bool
    #: For a secret: whether a value is stored. `value` stays `None` either way.
    is_set: bool = False
    #: The legal values for an enum, in display order. Empty otherwise.
    options: list[str] = []
    #: The panel heading within its section (`pricing.labor`, `general.farm`, …);
    #: `None` means the section is one undivided panel.
    group: str | None = None


class SectionView(BaseModel):
    """One heading of the rail, with its fields in order."""

    id: str
    fields: list[SettingView]


class SettingUpdate(BaseModel):
    """One edit.

    `value` is deliberately untyped here and parsed by the catalogue instead. A
    pydantic union across decimal, int, bool and enum would coerce — `True`
    arriving for a decimal field becomes `Decimal(1)` — and this is the one place
    where a quietly accepted wrong type ends up in every quote the farm gives.
    """

    value: Any


class SettingChangeView(BaseModel):
    """One row of «Было · Стало»."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    #: `None` means there was no row — the farm was on the code default. Not the
    #: same fact as "was the same number", and the screen should not render it so.
    old_value: Any | None
    #: `None` means it was reset back to the default.
    new_value: Any | None
    changed_at: datetime
    changed_by: EntityId | None
    #: The author's name, resolved by the API layer — the store keeps only the id,
    #: and a context may not reach into `identity` to look the name up itself.
    changed_by_name: str | None = None


__all__ = ["SectionView", "SettingChangeView", "SettingUpdate", "SettingView"]
