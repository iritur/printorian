"""The settings screen's fourteen editable sections, and every field they show.

Fourteen, not the kit's fifteen: diagnostics is a read-only health page with
nothing to edit, so `SECTION_ORDER` leaves it out.

This module is the *assembly*. What a setting is described in lives in `spec.py`
(`Kind`, `FieldSpec`, `Section`), and the parameters themselves live in
`declared.py` — derived from `RateSnapshot` and `SchedulingPolicy` where a
dataclass already states them, declared by hand where the kit names a parameter
no code consumes yet. The three were one file until it reached the 400-line gate;
splitting by responsibility rather than at the line the counter tripped is what
keeps each half nameable.

`Kind`, `FieldSpec` and `Section` are re-exported here on purpose: `catalogue.py`,
`service.py`, the package `__init__` and the docs tests all import them from
`sections`, and keeping that import path valid is what made the split a pure move
with no behavioural diff.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from printorian.contexts.settings.declared import (
    manual_specs,
    rate_specs,
    scheduling_weight_specs,
)
from printorian.contexts.settings.groups import GROUPS, in_group_order
from printorian.contexts.settings.spec import (
    FieldSpec,
    Kind,
    Section,
    default_finishes,
    default_tiers,
)


def _all_specs() -> list[FieldSpec]:
    specs: list[FieldSpec] = []
    specs += rate_specs()
    specs += scheduling_weight_specs()
    specs += manual_specs()
    return specs


#: key -> spec, in declaration order. Insertion order is what the screen shows, so
#: this is an ordinary dict rather than a sorted one — a sorted catalogue would put
#: `sla.min_lead_hours` before `sla.percent_per_day` and jumble every section.
FIELDS: Final[dict[str, FieldSpec]] = {
    spec.key: (replace(spec, group=GROUPS.get(spec.key)) if spec.key in GROUPS else spec)
    for spec in _all_specs()
}

#: The 15 sections, in rail order. Diagnostics (section 14) is deliberately absent:
#: it is a read-only health page, not settings, and has no fields to edit.
SECTION_ORDER: Final = (
    "general",
    "pricing",
    "discounts",
    "scheduling",
    "sla",
    "inventory",
    "service",
    "postprocess",
    "logistics",
    "finance",
    "notify",
    "security",
    "integrations",
    "maintenance",
)

#: Grouped rather than taken straight off `FIELDS`: a group split across the
#: section draws its panel heading twice — see `groups.in_group_order`.
SECTIONS: Final[tuple[Section, ...]] = tuple(
    Section(
        id=section_id,
        fields=in_group_order(spec.key for spec in FIELDS.values() if spec.section == section_id),
    )
    for section_id in SECTION_ORDER
)


__all__ = [
    "FIELDS",
    "SECTIONS",
    "SECTION_ORDER",
    "FieldSpec",
    "Kind",
    "Section",
    "default_finishes",
    "default_tiers",
]
