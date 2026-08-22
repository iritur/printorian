"""The farm's own settings, and the record of who changed them.

Two tables, and the second is not optional. «Было · Стало» is what lets an owner
answer *why a price changed last Tuesday* — the question a settings screen exists
to make answerable, and one that a table holding only current values cannot.

**Key/value rather than a column per setting.** There are about a hundred of them
across fifteen unrelated sections, they are heterogeneous — decimals, integers,
booleans, enum codes, tables of rows — and the set grows every time the farm
learns something. A column apiece means a migration apiece and a hundred-column
table; a row apiece means the schema stops changing. The cost is that the database
cannot type-check a value, so the catalogue in `catalogue.py` does it instead,
derived from the dataclass the value is going to end up in.

**An empty table means today's behaviour, exactly.** Nothing is seeded. A setting
with no row resolves to the code default it always had, so this migration changes
no farm's prices on the day it runs, and a row is only written when somebody
deliberately writes one. It is also what makes the feature revertible: delete the
row and the farm is back where it started.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from printorian.core.db import Base, Entity, JsonB, UtcDateTime
from printorian.core.ids import EntityId


class Setting(Base):
    """One overridden value, keyed by the name the catalogue knows it as.

    Not an `Entity`: the key *is* the identity. A surrogate id would allow two rows
    for `margin_percent` and leave which one wins to insertion order.
    """

    __tablename__ = "settings"
    __table_args__ = (
        # Every foreign key gets an index leading on it (`test_schema_contracts`).
        # `updated_by` is `ON DELETE SET NULL`, so without one the deletion of a
        # user scans this table — small today, and the gate exists because "small
        # today" is how V1 arrived at its sequential scans.
        Index("ix_settings_updated_by", "updated_by"),
    )

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    #: The value, in the JSON shape `catalogue.py` serializes it to. Decimals are
    #: stored as **strings**, for the reason `core.money` gives everywhere else: a
    #: JSON number is a float, and a float is not a price.
    value: Mapped[Any] = mapped_column(JsonB, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    #: Who wrote it. Nullable because a setting may be written by a migration or a
    #: script, and recording a fictional author would be worse than recording none.
    updated_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SettingChange(Entity):
    """One edit, kept for ever. Insert-only.

    Retained past the setting itself: the audit answers what the farm *was* doing,
    and deleting the history when a setting is reset to its default would erase
    exactly the period somebody is asking about. `ON DELETE SET NULL` on the author
    for the same reason — a departed employee must not take the record with them.
    """

    __tablename__ = "settings_audit"
    __table_args__ = (
        # The screen reads one key's history newest-first, and the audit log reads
        # the whole farm's. Both are covered by leading on the key with the clock
        # second, because a scan of the second is bounded by how often a farm
        # changes its own settings — which is rarely.
        Index("ix_settings_audit_key_changed_at", "key", "changed_at"),
        Index("ix_settings_audit_changed_at", "changed_at"),
        # As above: `changed_by` is a foreign key, and this history is the table
        # that grows.
        Index("ix_settings_audit_changed_by", "changed_by"),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    #: `None` when the setting had no row — the farm was on the code default, and
    #: "was: nothing" is a different fact from "was: the same number".
    old_value: Mapped[Any | None] = mapped_column(JsonB, nullable=True)
    #: `None` when the setting was reset, which is a real edit and gets a row.
    new_value: Mapped[Any | None] = mapped_column(JsonB, nullable=True)

    #: `Entity.created_at` is when the row was written and `changed_at` is when the
    #: edit happened. Normally the same instant; not the same fact, and a backfill
    #: or an import is exactly the case where they diverge.
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    changed_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["Setting", "SettingChange"]
