"""Reading and writing the farm's settings.

The rule the whole context is built around: **a key with no row is not a missing
setting, it is the code default.** Every read resolves through
`catalogue.DEFAULTS`, so an empty table behaves exactly as the farm behaved before
this context existed, and resetting a setting is a delete rather than a write-back
of a number somebody would then have to keep in step.

Purity is unaffected (ADR-0002). The pricing engine still *receives* a
`RateSnapshot` and never looks one up; `resolve_rates` builds it here, at the read
edge, exactly where `RateSnapshot()` used to be constructed from defaults. The
engine cannot tell the difference, which is the point.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.pricing import RateSnapshot
from printorian.contexts.settings import catalogue
from printorian.contexts.settings.models import Setting, SettingChange
from printorian.contexts.settings.schemas import SettingChangeView, SettingView
from printorian.core.clock import Clock
from printorian.core.ids import EntityId

#: Newest-first audit page size. The kit's «Обслуживание системы» log is a panel,
#: not an archive — the whole history is reachable, a screenful at a time.
HISTORY_LIMIT = 100


class SettingsService:
    """The settings table, read and written through the catalogue."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._db = session
        self._clock = clock

    # -- reading ---------------------------------------------------------

    async def overrides(self) -> dict[str, Any]:
        """Every key the farm has actually set, parsed into its own type.

        A row whose key the catalogue no longer knows is **skipped rather than
        raising**. Settings outlive the code that reads them: a rate that is
        renamed or retired leaves a row behind, and a farm that cannot start
        because of one is a farm held hostage by its own history. The row stays so
        the audit still resolves, and `listing` does not offer it.
        """
        rows = await self._db.scalars(select(Setting))
        resolved: dict[str, Any] = {}
        for row in rows:
            if catalogue.is_known(row.key):
                resolved[row.key] = catalogue.from_json(row.key, row.value)
        return resolved

    async def resolve_rates(self) -> RateSnapshot:
        """The rates a quote should be priced at right now.

        Defaults with the farm's overrides laid over them. Built with
        `dataclasses.replace` rather than by assignment because `RateSnapshot` is
        frozen — and it is frozen so that a snapshot pinned to an order cannot be
        edited afterwards, which is the guarantee ADR-0020 rests on.
        """
        overrides = await self.overrides()
        changed = {
            key.removeprefix(catalogue.RATE_PREFIX): value
            for key, value in overrides.items()
            if key.startswith(catalogue.RATE_PREFIX)
        }
        return dataclasses.replace(RateSnapshot(), **changed) if changed else RateSnapshot()

    async def listing(self) -> list[SettingView]:
        """Every known setting: its value, its default, and whether it was set.

        `is_overridden` is what the screen draws «БЫЛО» from, and it is a separate
        fact from the values being equal — a farm that deliberately set a rate to
        the number it already was has made a decision, and the audit records it.
        """
        overrides = await self.overrides()
        return [
            SettingView(
                key=key,
                value=catalogue.to_json(overrides.get(key, catalogue.default_for(key))),
                default=catalogue.to_json(catalogue.default_for(key)),
                is_overridden=key in overrides,
            )
            for key in catalogue.KEYS
        ]

    async def history(
        self, *, key: str | None = None, limit: int = HISTORY_LIMIT
    ) -> list[SettingChangeView]:
        """Recent edits, newest first, optionally for one key.

        Ordered by id as well as time, and the second term is not decoration: two
        edits within the same instant tie on `changed_at`, and a tie leaves the
        order to the planner. CI and this machine disagreed about which of two
        edits came first — the same query, the same rows, a different answer.

        `id` settles it correctly rather than merely consistently. It is a UUIDv7
        built from `time.time_ns()` (`core.ids`), which is the *real* clock rather
        than the injected one, so it stays chronological even where `Clock` is
        frozen and every `changed_at` in the table is identical.
        """
        query = (
            select(SettingChange)
            .order_by(SettingChange.changed_at.desc(), SettingChange.id.desc())
            .limit(limit)
        )
        if key is not None:
            query = query.where(SettingChange.key == key)
        rows = await self._db.scalars(query)
        return [SettingChangeView.model_validate(row) for row in rows]

    # -- writing ---------------------------------------------------------

    async def set_value(self, key: str, raw: Any, *, by: EntityId | None) -> SettingView:
        """Override one setting, recording what it was.

        Parsed before it is stored, so a bad value is refused at the edge rather
        than at the next quote — `from_json` raises rather than coercing, because a
        settings screen that turns `"30%"` into `30` has invented a number and it
        ends up in every price the farm gives.
        """
        parsed = catalogue.from_json(key, raw)
        stored = catalogue.to_json(parsed)
        now = self._clock.now()

        existing = await self._db.get(Setting, key)
        previous = existing.value if existing is not None else None
        if existing is None:
            self._db.add(Setting(key=key, value=stored, updated_at=now, updated_by=by))
        else:
            existing.value = stored
            existing.updated_at = now
            existing.updated_by = by

        self._record(key, old=previous, new=stored, at=now, by=by)
        await self._db.flush()
        return SettingView(
            key=key,
            value=stored,
            default=catalogue.to_json(catalogue.default_for(key)),
            is_overridden=True,
        )

    async def reset(self, key: str, *, by: EntityId | None) -> SettingView:
        """Drop the override and return to the code default.

        A real edit, and it gets an audit row: "back to the default" is an answer
        to *why did the price change* just as much as a new number is.
        """
        catalogue.default_for(key)  # raises NotFoundError for an unknown key
        now = self._clock.now()

        existing = await self._db.get(Setting, key)
        if existing is not None:
            await self._db.execute(delete(Setting).where(Setting.key == key))
            self._record(key, old=existing.value, new=None, at=now, by=by)
            await self._db.flush()

        return SettingView(
            key=key,
            value=catalogue.to_json(catalogue.default_for(key)),
            default=catalogue.to_json(catalogue.default_for(key)),
            is_overridden=False,
        )

    def _record(
        self, key: str, *, old: Any | None, new: Any | None, at: datetime, by: EntityId | None
    ) -> None:
        self._db.add(
            SettingChange(key=key, old_value=old, new_value=new, changed_at=at, changed_by=by)
        )


__all__ = ["HISTORY_LIMIT", "SettingsService"]
