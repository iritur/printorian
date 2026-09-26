"""A stocktake: the book on one side, what somebody actually counted on the other.

Issue #35's last measurable row. «Расхождения» on the store screen and the kit's
«Инвентаризация» panel both rest on this pair of tables, and neither can be
reconstructed later: a count that was not written down when the person stood at
the shelf is gone, in the same way a movement overwritten in place is.

**The book is snapshotted when the stocktake opens.** `expected_grams` is what
`MaterialLot.remaining_grams` said for each spool at that moment, copied — not a
join back to the lot, because the lot goes on changing while the count is under
way and the row has to keep saying what the book claimed on the day.

**A line that was not counted says so with a null.** `counted_grams` starts
absent and is written by the count alone. Defaulting it to zero would turn every
shelf nobody reached into a shortage of everything on it, which is the loudest
version of the ADR-0007 mistake. «Проверено 184 из 184» is counted from the
non-null lines, and the denominator is the lines that exist.

**The correction the count made is stored, not recomputed.** `variance_grams` is
written at close as ``counted − remaining at that moment`` and is the exact
amount the ledger row `stock.counted` carries. Recomputing it later from
`expected_grams` would give a different number whenever the spool was written
off between the opening and the close, and a report whose figures drift after
the fact is the thing this table exists to prevent.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.inventory.policies import StocktakeStatus
from printorian.core.db import Base, Entity, UtcDateTime, enum_column
from printorian.core.ids import EntityId

#: A sequence rather than ``count(*) + 1``, for the reason `sv_number_seq` is one.
STOCKTAKE_NUMBER_SEQUENCE = Sequence("st_number_seq", start=1, metadata=Base.metadata)


class Stocktake(Entity):
    """One count of the shelves, opened, filled in line by line, and closed."""

    __tablename__ = "stocktakes"
    __table_args__ = (
        UniqueConstraint("number", name="uq_stocktakes_number"),
        # The panel reads the latest; the service reads the open one. Both lead
        # with status and order by when it was opened.
        Index("ix_stocktakes_status_opened_at", "status", "opened_at"),
        # PostgreSQL does not index a foreign key for you.
        Index("ix_stocktakes_opened_by", "opened_by"),
        Index("ix_stocktakes_closed_by", "closed_by"),
        CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="closed_after_opened"),
    )

    number: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[StocktakeStatus] = mapped_column(
        enum_column(StocktakeStatus), nullable=False, default=StocktakeStatus.OPEN
    )
    #: The zone this count was limited to, as its code — copied text, so a zone
    #: renamed or retired afterwards leaves the record saying where people stood.
    #: Null for a count of the whole store.
    zone_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    opened_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``SET NULL`` on both, following `order_events.actor_id`: removing a member
    #: of staff must not erase the count they made.
    opened_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    lines: Mapped[list[StocktakeLine]] = relationship(
        back_populates="stocktake", cascade="all, delete-orphan", order_by="StocktakeLine.label"
    )


class StocktakeLine(Entity):
    """One spool as the book had it and as it was found."""

    __tablename__ = "stocktake_lines"
    __table_args__ = (
        # One line per spool per count; the leading column also serves the
        # `CASCADE` from the stocktake, so no separate index on `stocktake_id`.
        UniqueConstraint("stocktake_id", "lot_id", name="uq_stocktake_lines_stocktake_id_lot_id"),
        Index("ix_stocktake_lines_lot_id", "lot_id"),
        Index("ix_stocktake_lines_counted_by", "counted_by"),
        CheckConstraint("expected_grams >= 0", name="expected_non_negative"),
        CheckConstraint("counted_grams IS NULL OR counted_grams >= 0", name="counted_non_negative"),
        # The moment a count was written must exist exactly when the count does.
        CheckConstraint(
            "(counted_grams IS NULL) = (counted_at IS NULL)", name="counted_at_with_count"
        ),
    )

    stocktake_id: Mapped[EntityId] = mapped_column(
        ForeignKey("stocktakes.id", ondelete="CASCADE"), nullable=False
    )
    #: ``RESTRICT``: a closed stocktake's variance is the evidence a shortage rests
    #: on, and the ledger row it wrote already refuses the spool's deletion for the
    #: same reason (`material_movements.lot_id`).
    lot_id: Mapped[EntityId] = mapped_column(
        ForeignKey("material_lots.id", ondelete="RESTRICT"), nullable=False
    )
    #: Copied off the lot and its cell when the line was made, so the line reads
    #: without a join and keeps reading after the spool has moved on.
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    family: Mapped[str] = mapped_column(String(40), nullable=False)
    cell_address: Mapped[str | None] = mapped_column(String(24), nullable=True)

    #: The book at the moment the stocktake opened.
    expected_grams: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    #: What was found. Absent until somebody counts — never zero by default.
    counted_grams: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    counted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    counted_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Written at close: ``counted − remaining`` at that moment, signed. Negative is
    #: a shortage, positive a surplus, zero a match, null an uncounted line.
    variance_grams: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    stocktake: Mapped[Stocktake] = relationship(back_populates="lines")

    @property
    def is_counted(self) -> bool:
        return self.counted_grams is not None


__all__ = ["STOCKTAKE_NUMBER_SEQUENCE", "Stocktake", "StocktakeLine"]
