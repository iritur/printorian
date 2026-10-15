"""The stock that is not filament: packaging, post-processing consumables, spare parts.

Issue #35, decided by the owner as **one shared stock model**. Until now each class
kept its own count in its own context — `packaging_tara.stock`,
`postproduction_consumables.remaining` — written by restating an absolute number,
and spare parts had no stock at all. A box of film had no cell, a received carton
of nozzles had nowhere to go (`procurement.RECEIVABLE` refused it), and the
store's map, movements feed and stocktake knew only spools.

An **article** is one stocked item and its quantity on hand, in its own unit, in at
most one cell. The packing catalogue keeps what a box *is* — its inner size, its
price per parcel — and the article keeps how many of it there are; they meet on
``(kind, code)``, the same pair a purchase-order line already names
(`procurement.PurchaseOrderLine.item_code`), so neither context holds a foreign key
into the other's table.

**A ledger of its own rather than a widened `material_movements`.** That table is
shaped around a spool: ``grams``, ``remaining_after``, a sequence per lot. Putting
pieces and litres into a column named ``grams`` would be the bare-number-without-a-
unit mistake both catalogues' ``unit`` comments warn against. So articles get the
same shape in their own units, the same append-only rules, and the same reason
codes; the store's movements feed reads both (`store_views.movements`).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from printorian.contexts.inventory.policies import ArticleKind
from printorian.core.db import Entity, UtcDateTime, enum_column
from printorian.core.ids import EntityId

#: Back on the shelf after a correction — a packer chose a different box, and the
#: first one goes back. Only articles use it: a spool is never "un-printed".
MOVED_RETURNED = "stock.returned"


class StockArticle(Entity):
    """One stocked item that is not filament, and how many of it the farm holds."""

    __tablename__ = "stock_articles"
    __table_args__ = (
        UniqueConstraint("kind", "code", name="uq_stock_articles_kind_code"),
        Index("ix_stock_articles_cell_id", "cell_id"),
        CheckConstraint("on_hand >= 0", name="on_hand_non_negative"),
        CheckConstraint("reorder_at >= 0", name="reorder_at_non_negative"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_non_negative"),
    )

    kind: Mapped[ArticleKind] = mapped_column(enum_column(ArticleKind), nullable=False)
    #: Unique within its kind: a packing «A» box and a spare part «A» are two things.
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: A unit *code* — `piece`, `roll`, `sheet`, `litre`, `kg` — rendered by the
    #: client, as both catalogues this replaces already did.
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="piece")
    on_hand: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal(0))
    #: The level the reorder list starts at. Zero disables it.
    reorder_at: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal(0))
    #: What one cost on the last receipt that recorded a price. **Null until then**:
    #: an article stocked before receiving existed has no measured cost, and a zero
    #: would make dead stock read as free (ADR-0007).
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: ``SET NULL``, as for a spool: retiring a cell unplaces what was in it. The
    #: ledger keeps the address as text, so the history survives.
    cell_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("storage_cells.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ArticleMovement(Entity):
    """One movement of one article — `MaterialMovement`'s shape, in the article's unit."""

    __tablename__ = "article_movements"
    __table_args__ = (
        # The same per-item ordering `material_movements` keeps, for the same
        # reason: a millisecond does not order two rows, and a losing race should be
        # an integrity error rather than two rows claiming one position.
        UniqueConstraint("article_id", "sequence", name="uq_article_movements_article_id_sequence"),
        Index("ix_article_movements_actor_id", "actor_id"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        CheckConstraint("on_hand_after >= 0", name="on_hand_after_non_negative"),
    )

    #: ``RESTRICT``: a history that can be erased by deleting what it describes is
    #: not a record. Articles are retired with `is_active`, never deleted.
    article_id: Mapped[EntityId] = mapped_column(
        ForeignKey("stock_articles.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: A `movements.MOVEMENT_REASONS` code, or `MOVED_RETURNED`.
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    #: How much moved, in the article's unit. Always the size, never signed;
    #: `on_hand_after` says which way it went.
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal(0))
    on_hand_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    actor_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Copied text, not keys — the reasoning `movements` gives at length.
    from_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: What it was for: a purchase order's number, a parcel's, a ticket's. The kit's
    #: «Основание» column, and «каждое движение привязано к заявке или заказу».
    reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


__all__ = ["MOVED_RETURNED", "ArticleMovement", "StockArticle"]
