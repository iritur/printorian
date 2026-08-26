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

**Secrets are write-only, and encrypted at rest (ADR-0014).** `finance.yookassa_secret_key`
is never read back and never appears in the audit: a replaced secret records «was
set · now set», not the value, because the value of the audit is answering *who
changed it and when*, and a stored key would make a routine dump carry a payment
credential.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import PromisePolicy
from printorian.contexts.pricing import CustomerTier, RateSnapshot
from printorian.contexts.scheduling import SchedulingPolicy
from printorian.contexts.settings import catalogue
from printorian.contexts.settings.models import Setting, SettingChange
from printorian.contexts.settings.schemas import SectionView, SettingChangeView, SettingView
from printorian.contexts.settings.sections import FIELDS, SECTIONS, Kind, default_tiers
from printorian.core.clock import Clock
from printorian.core.errors import ConfigurationError, NotFoundError
from printorian.core.ids import EntityId
from printorian.core.secrets import SecretBox

#: Newest-first audit page size. The kit's «Обслуживание системы» log is a panel,
#: not an archive — the whole history is reachable, a screenful at a time.
HISTORY_LIMIT = 100


class SettingsService:
    """The settings table, read and written through the catalogue."""

    def __init__(
        self, session: AsyncSession, clock: Clock, *, secret_box: SecretBox | None = None
    ) -> None:
        self._db = session
        self._clock = clock
        self._secret_box = secret_box

    # -- reading ---------------------------------------------------------

    async def overrides(self) -> dict[str, Any]:
        """Every key the farm has actually set, parsed into its own type.

        A row whose key the catalogue no longer knows is **skipped rather than
        raising**, and so is a secret — settings outlive the code that reads them,
        and a ciphertext value is never useful to a resolver. The retired row stays
        so the audit still resolves, and `listing` does not offer it.
        """
        rows = await self._db.scalars(select(Setting))
        resolved: dict[str, Any] = {}
        for row in rows:
            spec = FIELDS.get(row.key)
            if spec is None or spec.kind is Kind.SECRET:
                continue
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

    async def resolve_promise(self) -> PromisePolicy:
        """The lead-time policy a quote should promise against right now.

        The same read-edge shape as `resolve_rates`: defaults with the farm's
        overrides laid over them, so an empty table promises exactly what the farm
        always promised, and a changed `sla.min_lead_hours` moves the next quote
        and nothing already agreed.
        """
        overrides = await self.overrides()
        mapping = {
            "sla.promise_buffer_percent": "promise_buffer_percent",
            "sla.min_lead_hours": "min_lead_hours",
            "sla.rush_lead_hours": "rush_lead_hours",
        }
        changed = {
            field_name: overrides[key] for key, field_name in mapping.items() if key in overrides
        }
        return dataclasses.replace(PromisePolicy(), **changed) if changed else PromisePolicy()

    async def resolve_scheduling(self) -> SchedulingPolicy:
        """The scheduler weights a planning pass should use right now.

        Derived from the dataclass's own fields, not a hand-listed set, for the
        same reason the catalogue is: a weight added to `SchedulingPolicy`
        appears here without a second place to remember. The other `scheduling.*`
        keys — the tick interval and the wait-list behaviour — are not planner
        weights and are deliberately left out.
        """
        overrides = await self.overrides()
        changed = {
            field.name: overrides[f"scheduling.{field.name}"]
            for field in dataclasses.fields(SchedulingPolicy)
            if f"scheduling.{field.name}" in overrides
        }
        return dataclasses.replace(SchedulingPolicy(), **changed) if changed else SchedulingPolicy()

    async def resolve_int(self, key: str) -> int:
        """The resolved value of one integer setting — override, else the default."""
        overrides = await self.overrides()
        return int(overrides.get(key, catalogue.default_for(key)))

    async def resolve_tiers(self) -> dict[str, CustomerTier]:
        """The customer tiers (discount + margin override), keyed by code.

        Defaults from the loyalty ladder, with the farm's overrides laid over. The
        `from_spend` thresholds that *earn* a tier stay in `loyalty.py` — the kit's
        table shows the price book, not how a tier is earned.
        """
        overrides = await self.overrides()
        tiers = overrides.get("pricing.tiers", default_tiers())
        return {tier.code: tier for tier in tiers}

    async def listing(self) -> list[SettingView]:
        """Every known setting, in section order, with its default beside it.

        `is_overridden` is what the screen draws «БЫЛО» from, and it is a separate
        fact from the values being equal — a farm that deliberately set a rate to
        the number it already was has made a decision, and the audit records it.

        A secret's `value` and `default` are `None`; `is_set` is the only fact
        about it that leaves the store.
        """
        rows = list(await self._db.scalars(select(Setting)))
        stored = {row.key: row for row in rows}

        views: list[SettingView] = []
        for key, spec in FIELDS.items():
            if spec.kind is Kind.SECRET:
                views.append(
                    SettingView(
                        key=key,
                        section=spec.section,
                        kind=spec.kind.value,
                        value=None,
                        default=None,
                        is_overridden=key in stored,
                        is_set=key in stored,
                        options=list(spec.options),
                        group=spec.group,
                    )
                )
                continue
            value = stored[key].value if key in stored else spec.default
            views.append(
                SettingView(
                    key=key,
                    section=spec.section,
                    kind=spec.kind.value,
                    value=catalogue.to_json(value),
                    default=catalogue.to_json(spec.default),
                    is_overridden=key in stored,
                    options=list(spec.options),
                    group=spec.group,
                )
            )
        return views

    async def sections(self) -> list[SectionView]:
        """The screen's fourteen headings, each with its fields in order.

        Fourteen and not the kit's fifteen: diagnostics is a read-only health
        page with nothing to edit, so `SECTION_ORDER` leaves it out.
        """
        by_key = {view.key: view for view in await self.listing()}
        return [
            SectionView(id=section.id, fields=[by_key[key] for key in section.fields])
            for section in SECTIONS
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
        spec = FIELDS.get(key)
        if spec is None:
            raise NotFoundError("error.settings.unknown_key", key=key)

        parsed = catalogue.from_json(key, raw)
        now = self._clock.now()

        existing = await self._db.get(Setting, key)
        previous = existing.value if existing is not None else None

        if spec.kind is Kind.SECRET:
            if self._secret_box is None:
                raise ConfigurationError(
                    "error.settings.no_secret_box",
                    hint="a secret cannot be stored without PRINTORIAN_SECRET_KEY",
                )
            stored: Any = self._secret_box.encrypt(parsed)
            self._record(key, old=None, new=None, at=now, by=by)
        else:
            stored = catalogue.to_json(parsed)
            self._record(key, old=previous, new=stored, at=now, by=by)

        if existing is None:
            self._db.add(Setting(key=key, value=stored, updated_at=now, updated_by=by))
        else:
            existing.value = stored
            existing.updated_at = now
            existing.updated_by = by

        await self._db.flush()

        if spec.kind is Kind.SECRET:
            return SettingView(
                key=key,
                section=spec.section,
                kind=spec.kind.value,
                value=None,
                default=None,
                is_overridden=True,
                is_set=True,
                options=list(spec.options),
            )
        return SettingView(
            key=key,
            section=spec.section,
            kind=spec.kind.value,
            value=stored,
            default=catalogue.to_json(spec.default),
            is_overridden=True,
            options=list(spec.options),
        )

    async def reset(self, key: str, *, by: EntityId | None) -> SettingView:
        """Drop the override and return to the code default.

        A real edit, and it gets an audit row: "back to the default" is an answer
        to *why did the price change* just as much as a new number is.
        """
        spec = FIELDS.get(key)
        if spec is None:
            raise NotFoundError("error.settings.unknown_key", key=key)

        now = self._clock.now()
        existing = await self._db.get(Setting, key)
        if existing is not None:
            await self._db.execute(delete(Setting).where(Setting.key == key))
            if spec.kind is Kind.SECRET:
                self._record(key, old=None, new=None, at=now, by=by)
            else:
                self._record(key, old=existing.value, new=None, at=now, by=by)
            await self._db.flush()

        if spec.kind is Kind.SECRET:
            return SettingView(
                key=key,
                section=spec.section,
                kind=spec.kind.value,
                value=None,
                default=None,
                is_overridden=False,
                is_set=False,
                options=list(spec.options),
            )
        return SettingView(
            key=key,
            section=spec.section,
            kind=spec.kind.value,
            value=catalogue.to_json(spec.default),
            default=catalogue.to_json(spec.default),
            is_overridden=False,
            options=list(spec.options),
        )

    async def reset_prefix(self, prefix: str, *, by: EntityId | None) -> int:
        """Drop every override under a prefix, auditing each one as its own reset.

        The one irreversible operation the screen offers that is safe: rates are
        resolved at the read edge and orders pin their snapshots (ADR-0020), so
        deleting the overrides returns the farm to the code defaults for the *next*
        quote and touches nothing already sold. Each deleted row records «was · now
        default» under the author, so the reset is answerable afterwards like any
        other edit.
        """
        rows = list(await self._db.scalars(select(Setting).where(Setting.key.like(f"{prefix}%"))))
        now = self._clock.now()
        for row in rows:
            await self._db.execute(delete(Setting).where(Setting.key == row.key))
            self._record(row.key, old=row.value, new=None, at=now, by=by)
        if rows:
            await self._db.flush()
        return len(rows)

    def _record(
        self, key: str, *, old: Any | None, new: Any | None, at: datetime, by: EntityId | None
    ) -> None:
        self._db.add(
            SettingChange(key=key, old_value=old, new_value=new, changed_at=at, changed_by=by)
        )


__all__ = ["HISTORY_LIMIT", "SettingsService"]
