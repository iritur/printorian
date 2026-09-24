"""«Периодичность по умолчанию» — the service intervals a new machine starts with.

Settings section 07's table (issue #29), and the one of its six that has a
consumer waiting: until now a printer was registered with an empty service card,
and every operation an engineer then added defaulted to `500` hours from a
literal in `CreateServiceOperation`. The kit's panel says «ПРИМЕНЯЕТСЯ К НОВЫМ
МАШИНАМ», which is exactly what `seed_service_card` does — and
`api/routers/printers.py` reads the same table when an operation is added
without a periodicity, so the literal is gone from both paths.

The shape lives here, in fleet, for the reason `ZoneTariffs` lives in pricing
rather than in settings: the context that consumes a table owns what a valid row
is. Settings stores and audits it; it does not get to decide that `nozzle_change`
is a kind of maintenance.

**Two of the kit's five columns are deliberately not stored.** «Простой» (the
downtime an operation costs) and «Расход» (what it costs in rubles) have no
reader anywhere — `ServiceOperation` carries neither, and nothing prices a
service. A settings row nothing reads is the failure CLAUDE.md §1 warns about
wearing a nicer hat, so the table is the two columns the farm consumes, and the
other two arrive with the thing that reads them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet.models import ServiceOperation
from printorian.contexts.fleet.policies import MaintenanceKind
from printorian.core.errors import ValidationError
from printorian.core.ids import EntityId

#: What `CreateServiceOperation` defaulted to before this table existed. The
#: table's own default is every kind at this figure, so a farm that has never
#: opened the panel gets precisely the card it always got.
LEGACY_INTERVAL_HOURS = 500

KEY = "service.maintenance_defaults"


@dataclass(frozen=True, slots=True)
class MaintenanceDefault:
    """One row: an operation and how many printing hours between occurrences."""

    kind: MaintenanceKind
    interval_hours: int

    def __post_init__(self) -> None:
        if self.interval_hours < 1:
            # Zero would put an operation permanently overdue on every new
            # machine; a negative one is a typo. Neither is an interval.
            raise ValidationError(
                "error.fleet.maintenance_interval_invalid",
                code=self.kind.value,
                interval_hours=self.interval_hours,
            )


@dataclass(frozen=True, slots=True)
class MaintenanceDefaults:
    """The table. Codes are unique, and every code is a kind the fleet knows."""

    rows: tuple[MaintenanceDefault, ...]

    def __post_init__(self) -> None:
        codes = [row.kind.value for row in self.rows]
        duplicated = sorted({code for code in codes if codes.count(code) > 1})
        if duplicated:
            # Two rows for one kind do not merge into one answer: `hours_for`
            # would return whichever came last, and the row an owner edited and
            # the interval a new machine got could differ by a scroll position.
            raise ValidationError("error.fleet.maintenance_kind_duplicate", codes=duplicated)

    def hours_for(self, kind: MaintenanceKind) -> int | None:
        """The interval for one kind, or ``None`` when the table does not seed it."""
        return next((row.interval_hours for row in self.rows if row.kind is kind), None)


def default_maintenance() -> MaintenanceDefaults:
    """Every kind the fleet knows, at the interval the code always assumed."""
    return MaintenanceDefaults(
        rows=tuple(
            MaintenanceDefault(kind=kind, interval_hours=LEGACY_INTERVAL_HOURS)
            for kind in MaintenanceKind
        )
    )


def parse_maintenance_defaults(raw: Any) -> MaintenanceDefaults:
    """The settings screen's body, as rows of ``{code, interval_hours}``.

    Shape errors — not a list, a row missing a key — are the screen's and are
    reported as `error.settings.not_a_table` by the caller in `settings.catalogue`;
    this raises the fleet's own codes for the things only the fleet can judge: a
    kind it has never heard of, a duplicate, an interval that is not one.
    """
    if not isinstance(raw, list):
        raise TypeError("a maintenance table is a list of rows")
    rows: list[MaintenanceDefault] = []
    for item in raw:
        code = str(item["code"])
        try:
            kind = MaintenanceKind(code)
        except ValueError:
            raise ValidationError(
                "error.fleet.maintenance_kind_unknown",
                code=code,
                known=[known.value for known in MaintenanceKind],
            ) from None
        hours = item["interval_hours"]
        # `bool` is an `int` subclass, and `true` is not an interval.
        if isinstance(hours, bool) or not isinstance(hours, int):
            raise TypeError("interval_hours must be an integer")
        rows.append(MaintenanceDefault(kind=kind, interval_hours=hours))
    return MaintenanceDefaults(rows=tuple(rows))


def maintenance_to_json(value: MaintenanceDefaults) -> list[dict[str, Any]]:
    """The wire and column shape — the same rows `parse_maintenance_defaults` reads."""
    return [{"code": row.kind.value, "interval_hours": row.interval_hours} for row in value.rows]


async def seed_service_card(
    db: AsyncSession,
    *,
    printer_id: EntityId,
    printed_hours: Decimal,
    defaults: MaintenanceDefaults,
    existing: Sequence[MaintenanceKind] = (),
) -> int:
    """Give a machine one operation per row of the table, clocks set to now.

    ``existing`` is the kinds already on the card, so calling this on a machine
    that has one is additive rather than a duplicate: the card the engineer
    started by hand keeps its rows, and the table fills in the rest. Returns how
    many rows were added, which is what the caller's test asserts on.

    Flushed here rather than committed: the caller's request-scoped session owns
    the transaction, so a registration whose seeding fails is a registration that
    did not happen — never a machine on the floor with half a card.
    """
    added = 0
    for row in defaults.rows:
        if row.kind in existing:
            continue
        db.add(
            ServiceOperation(
                printer_id=printer_id,
                kind=row.kind,
                interval_hours=row.interval_hours,
                last_done_at_hours=printed_hours,
                materials_used=[],
                notes=None,
            )
        )
        added += 1
    await db.flush()
    return added


__all__ = [
    "KEY",
    "LEGACY_INTERVAL_HOURS",
    "MaintenanceDefault",
    "MaintenanceDefaults",
    "default_maintenance",
    "maintenance_to_json",
    "parse_maintenance_defaults",
    "seed_service_card",
]
