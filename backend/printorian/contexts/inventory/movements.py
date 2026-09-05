"""Every time a lot moved, and what it was worth to know afterwards.

`MaterialLot` answers *where is this spool* with five columns it overwrites in
place — `location_kind`, `printer_id`, `ams_unit`, `ams_slot`, `shelf`. That is
the right shape for the question the scheduler asks and the wrong shape for every
other one: mounting a spool destroys the record of the cell it came out of, and
"who took 400 g off this reel and why" has no answer at all once the column has
been rewritten. The field says where; nothing said how it got there.

This is the ledger half. Append-only: nothing updates a row here and nothing
deletes one, which is why the foreign key below is ``RESTRICT`` rather than
``CASCADE`` — a stock history that can be rewritten by deleting the spool it
describes is not a record.

**Why the two sides are copied text and not foreign keys.** A movement names the
place a spool left and the place it arrived. Pointing those at `storage_cells`
would leave two bad options: ``SET NULL``, which erases the one answer the row
exists to give the moment a cell is retired, or ``RESTRICT``, which makes retiring
a cell impossible for as long as the history is kept. Copying the address costs
nothing and survives both. It is the same call `PackStep` made for instruction
text and `SlaCreditEntry` made for its decay terms, and it is what lets
``A1-2 → брак`` be recorded without inventing a table for «брак».
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from printorian.contexts.inventory.policies import LocationKind
from printorian.core.db import Entity, UtcDateTime, enum_column
from printorian.core.ids import EntityId

#: A lot entered the farm's stock for the first time.
MOVED_RECEIVED = "stock.received"
#: Cell to cell, with the spool staying in storage.
MOVED_MOVED = "stock.moved"
#: Handed to production without being consumed yet.
MOVED_ISSUED = "stock.issued"
#: Mass left the reel and is not coming back — the irreversible one.
MOVED_WRITTEN_OFF = "stock.written_off"
#: Loaded into a printer's AMS slot.
MOVED_MOUNTED = "stock.mounted"
#: Taken back out of a printer.
MOVED_UNMOUNTED = "stock.unmounted"

#: Every reason a row may carry. Machine-readable, rendered by the client
#: (ADR-0012); the column is a `String` rather than an enum because the set grows
#: with receiving and stocktake, and an `ALTER TYPE` is a worse migration than a
#: new constant here.
MOVEMENT_REASONS: frozenset[str] = frozenset(
    {
        MOVED_RECEIVED,
        MOVED_MOVED,
        MOVED_ISSUED,
        MOVED_WRITTEN_OFF,
        MOVED_MOUNTED,
        MOVED_UNMOUNTED,
    }
)


class MaterialMovement(Entity):
    """One movement of one lot, and everything needed to read it years later.

    Deliberately not reachable from `MaterialLot`: there is no relationship on the
    lot pointing here, for the reason `SlaCreditEntry` records at length. The
    materials table eagerly loads every live lot of every spec on one page, and a
    lot that has been mounted and unmounted daily for a year carries several
    hundred movements. The ledger is written far more often than a lot's history
    is read, and it is read by query — by cell, by date, by lot — rather than by
    traversal from the thing it describes.
    """

    __tablename__ = "material_movements"
    __table_args__ = (
        # The same ordering guarantee `sla_credit_entries` needed, for the same
        # reason: `created_at` has no sub-second granularity to rely on and the
        # UUIDv7 key only orders to the millisecond, so two rows written inside one
        # millisecond sort by their random bits. It also turns a losing race
        # between two writers into an integrity error rather than two rows both
        # claiming to be the third movement of this spool.
        UniqueConstraint("lot_id", "sequence", name="uq_material_movements_lot_id_sequence"),
        # No separate index on `lot_id`: the constraint above is one, and its
        # leading column is `lot_id`, so both the per-lot read and the `RESTRICT`
        # check are served by it. `actor_id` has no such cover, and the `SET NULL`
        # that fires when a member of staff is removed would scan the whole ledger.
        Index("ix_material_movements_actor_id", "actor_id"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        # A movement of nothing is noise in a ledger whose whole point is to say
        # when something moved — `credit_actually_moved` next door, one context on.
        CheckConstraint("grams >= 0", name="grams_non_negative"),
        CheckConstraint("remaining_after >= 0", name="remaining_after_non_negative"),
    )

    #: ``RESTRICT``, and the cost of it is worth knowing before it is met.
    #:
    #: The ledger is what stock history is computed from, so deleting a spool out
    #: from under it silently rewrites the record — the identical call
    #: `PackUse.tara_id` made one context over. The cost: `MaterialSpec.lots`
    #: carries ``cascade="all, delete-orphan"``, so once a lot has moved,
    #: ORM-deleting its **spec** raises `IntegrityError`. That is safe today —
    #: nothing in the system deletes a spec, only deactivates it — and this
    #: sentence is here so the next person meets the fact in the model rather than
    #: in a stack trace.
    lot_id: Mapped[EntityId] = mapped_column(
        ForeignKey("material_lots.id", ondelete="RESTRICT"), nullable=False
    )
    #: 1-based position in this lot's history. The only dependable order.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: One of `MOVEMENT_REASONS`.
    reason: Mapped[str] = mapped_column(String(40), nullable=False)

    #: The mass that moved. Zero for a pure relocation — the spool changed place
    #: and not mass — and positive for a write-off, which is the only reason in
    #: this slice that reduces the reel.
    grams: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    #: What was left on the reel once this movement was applied. Stored rather than
    #: recomputed, so a row still reads correctly after the lot itself has moved on.
    remaining_after: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    #: The moment the movement happened, from the injected clock. `created_at` says
    #: when the row was written, which is the same thing right up until a backlog
    #: is replayed.
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    #: Who moved it. ``SET NULL``, following `order_events.actor_id`: removing a
    #: member of staff must not delete the record of what was moved and why, and
    #: *who* is the one part of that record the farm accepts losing.
    actor_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    #: Where it came from and where it went, as the operator would read them.
    #:
    #: Null on a side that has no address rather than carrying a placeholder: a
    #: lot received into the farm came from nowhere this system knows, and a spool
    #: mounted into a machine goes to a place `inventory` may not name — naming a
    #: fleet row from here is exactly the import the layering forbids, and a UUID
    #: is not an address a person reads. The machine is on the lot, which is where
    #: "where is it now" is asked; this row's job is the cell it *left*.
    #: ``String(60)`` rather than the cell's ``String(24)``, and the difference is
    #: the point: `MaterialLot.shelf` is 60 characters of free text and a spool
    #: unmounted onto one is recorded here verbatim. Truncating it to an address
    #: width would store a place nobody wrote down, which is the small end of the
    #: same mistake as inventing one.
    from_kind: Mapped[LocationKind | None] = mapped_column(enum_column(LocationKind), nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    to_kind: Mapped[LocationKind | None] = mapped_column(enum_column(LocationKind), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(60), nullable=True)

    #: Free text an operator typed — the «Основание» column of the kit's movements
    #: table. Optional, and never a substitute for `reason`: the client renders the
    #: code and shows this beside it (ADR-0012).
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
