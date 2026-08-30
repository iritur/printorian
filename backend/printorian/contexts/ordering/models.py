"""Persistent models for orders.

The price is **pinned**: the breakdown the customer agreed to is stored verbatim on
the order, alongside the rate-snapshot id and engine version that produced it. The
order is never repriced by re-running the engine — that is what makes an old quote
reproducible instead of merely plausible (ADR-0002).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.ordering.policies import (
    OPEN_STATUSES,
    TRANSITIONS,
    DeliveryMethod,
    OrderStatus,
)
from printorian.core.db import Base, Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId

#: Rendered into the partial index below. A literal rather than a bound parameter
#: because index predicates are DDL, and DDL takes no parameters.
_OPEN_STATUS_SQL = ", ".join(f"'{status.value}'" for status in sorted(OPEN_STATUSES))

#: Where human-quotable order numbers come from.
#:
#: They used to come from ``SELECT count(*) FROM orders``, which is wrong twice: two
#: checkouts running together read the same count and produce the same number, so
#: the unique constraint turns one of them into a 500 at the exact moment a customer
#: is paying — and the cost of creating order #50,000 was proportional to 50,000.
#:
#: A sequence is transactional but *not* transaction-scoped: two callers never get
#: the same value, and neither waits for the other. Gaps appear when a checkout is
#: rolled back, which is the accepted trade — a gap in the numbering is a cosmetic
#: fact, a collision is a lost sale.
#:
#: Attached to the metadata so `create_all` and Alembic both know about it. SQLite
#: reports no sequence support, so the DDL is skipped there and `_next_number` falls
#: back for the test dialect.
ORDER_NUMBER_SEQUENCE = Sequence("order_number_seq", start=1, metadata=Base.metadata)


class RateSnapshotRecord(Base):
    """The rates one quote was built from, kept so the quote can be rebuilt.

    ADR-0002 promises that an order's price can be recomputed years later. The hash
    on the order was never enough on its own: it proves *which* rates were used and
    detects tampering, but the values behind it lived only in code, so changing a
    rate made every older hash unresolvable. This is where they live now.

    It belongs to `ordering` rather than `pricing` because `pricing` may not touch a
    database (ADR-0002), and `ordering` is the context that pins the reference.

    Insert-only, and never deleted while an order references it — ``orders`` holds a
    ``RESTRICT`` foreign key precisely so a cleanup job cannot quietly strand one.
    The primary key is the content hash rather than a UUID: identical rates *are*
    the same snapshot, so writing them twice must collapse to one row.
    """

    __tablename__ = "rate_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: The whole `RateSnapshot`, as `pricing.serialization.rates_to_dict` renders it.
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    #: The engine that read these rates. A snapshot alone does not fix the result;
    #: the calculation shape has to be pinned with it.
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), nullable=False
    )


class Order(Entity):
    """What a customer bought."""

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_status_created_at", "status", "created_at"),
        Index("ix_orders_customer_id_created_at", "customer_id", "created_at"),
        # The order desk looks at open work. Closed orders are the overwhelming
        # majority of the table within a year and none of them are ever on that
        # screen, so a partial index stays roughly constant in size while the full
        # one above grows with all history.
        Index(
            "ix_orders_open_created_at",
            "created_at",
            postgresql_where=text(f"status IN ({_OPEN_STATUS_SQL})"),
        ),
        # A `RESTRICT` foreign key is checked on every delete of the parent, and
        # without an index that check is a full scan of this table.
        Index("ix_orders_rate_snapshot_id", "rate_snapshot_id"),
        CheckConstraint("total >= 0", name="total_non_negative"),
        CheckConstraint("sla_credit >= 0", name="sla_credit_non_negative"),
        # A credit larger than the order would refund more than was ever collected.
        CheckConstraint("sla_credit <= total", name="sla_credit_within_total"),
        # The same three rules `DecayPolicy.__post_init__` enforces, restated where
        # the values actually live. The dataclass guards what the application
        # writes; these guard what the column can hold, which is the half that
        # survives a bad backfill or a hand-run UPDATE.
        CheckConstraint("decay_percent_per_day >= 0", name="decay_percent_per_day_non_negative"),
        CheckConstraint("decay_grace_seconds >= 0", name="decay_grace_seconds_non_negative"),
        CheckConstraint(
            "decay_max_percent >= 0 AND decay_max_percent <= 100",
            name="decay_max_percent_within_range",
        ),
        # All three or none. A half-pinned order reads as pinned and then needs a
        # live lookup for the missing half — which is the reprice these columns
        # exist to prevent, reintroduced through the back door.
        CheckConstraint(
            "num_nonnulls(decay_percent_per_day, decay_grace_seconds, decay_max_percent) IN (0, 3)",
            name="decay_terms_all_or_none",
        ),
    )

    number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        enum_column(OrderStatus), nullable=False, default=OrderStatus.DRAFT
    )
    customer_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Kept even if the account is later removed, so the order stays explicable.
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal(0))

    # -- the pinned price ------------------------------------------------
    #: Serialized Breakdown (pricing.serialization). Never recomputed in place.
    price_breakdown: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)
    #: The rates this quote was built from. A real foreign key, so the snapshot
    #: cannot be dropped out from under an order that depends on it — which is what
    #: makes ADR-0002's "recompute it years later" a fact rather than a hope.
    #: Null only for orders written before the snapshot table existed; every order
    #: placed since carries one.
    rate_snapshot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("rate_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    # -- delivery promise ------------------------------------------------
    promised_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    decay_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")

    #: The decay terms as they stood at checkout — ADR-0020 applied to the promise
    #: rather than to the price.
    #:
    #: The code above was never enough on its own. It names a rule whose numbers
    #: lived only in `POLICIES`, and `_credit_for` re-read that dict on every
    #: sweep, so raising `standard` from 5%/day to 10%/day did not apply to new
    #: orders — it re-priced every promise already sold, on the next pass of the
    #: worker. The customer agreed to one number and was owed another, and the
    #: order row recorded nothing that could say which.
    #:
    #: Null only for orders written before the terms were pinned, which is exactly
    #: the shape `rate_snapshot_id` uses above and is read the same way: the
    #: service falls back to the live policy for those, and pins nothing new.
    decay_percent_per_day: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: Seconds rather than an interval: the grace is compared against a Python
    #: `timedelta` in the pure policy object and never in SQL, so an integer needs
    #: no dialect-specific type and cannot arrive as a month-bearing interval that
    #: has no fixed length.
    decay_grace_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decay_max_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    #: Credit accrued for lateness, in currency units. Settled at refund time.
    sla_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal(0))

    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Where it goes. Kept on the order rather than on the customer: people ship to
    # an office once and home the next time, and overwriting the previous address
    # would quietly re-route an order already in production.
    delivery_method: Mapped[DeliveryMethod] = mapped_column(
        String(16), nullable=False, default=DeliveryMethod.PICKUP
    )
    delivery_city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    delivery_postcode: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    delivery_address: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    notify_on_progress: Mapped[bool] = mapped_column(nullable=False, default=True)

    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    #: Ordered oldest-first by explicit sequence. Neither `created_at` nor the
    #: UUIDv7 primary key is reliable here: SQLite's CURRENT_TIMESTAMP has
    #: second granularity, and UUIDv7 only orders at millisecond granularity —
    #: two events written in the same millisecond sort by their random bits.
    events: Mapped[list[OrderEvent]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.sequence",
    )

    @property
    def allowed_transitions(self) -> list[OrderStatus]:
        """Where this order may legally go next.

        Exposed so the order desk can offer exactly the moves the state machine
        permits. The alternative — shipping `TRANSITIONS` to the client — would
        put the same rules in two languages, and the day they disagree the UI
        offers a button that the API refuses (ADR-0015 applies the same reasoning
        to live events).
        """
        return sorted(TRANSITIONS[self.status])


class OrderLine(Entity):
    """One configured model within an order."""

    __tablename__ = "order_lines"
    __table_args__ = (
        Index("ix_order_lines_order_id", "order_id"),
        # The index the `RESTRICT` check reads: without it, deciding whether a
        # model may be collected scans every order line ever written.
        Index("ix_order_lines_model_asset_id", "model_asset_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("scale > 0", name="scale_positive"),
        CheckConstraint("line_total >= 0", name="line_total_non_negative"),
        CheckConstraint("estimated_minutes >= 0", name="estimated_minutes_non_negative"),
        CheckConstraint("estimated_grams >= 0", name="estimated_grams_non_negative"),
    )

    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    #: What the customer called the file. A label, shown to people.
    model_name: Mapped[str] = mapped_column(String(300), nullable=False)
    #: The geometry this line was priced from, and the only thing that identifies
    #: it. `model_name` cannot: two customers uploading different parts as
    #: `part.stl` are otherwise indistinguishable, and the same part under two names
    #: would be sliced twice.
    #:
    #: ``RESTRICT``, and that is the whole of the protection retention needs: the
    #: database refuses to collect geometry an order still has to print, so the
    #: sweep in `catalog.assets` does not have to ask `ordering` anything.
    #:
    #: Nullable for orders placed before the library existed, and for the manual
    #: order desk, where a job may be entered against a model the farm holds
    #: physically rather than as a file.
    model_asset_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("model_assets.id", ondelete="RESTRICT"), nullable=True
    )
    material_code: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scale: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal(1))
    rush: Mapped[bool] = mapped_column(nullable=False, default=False)

    #: Chosen colours and finishes, as the configurator recorded them.
    colors: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)
    finishes: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)

    #: Geometry and the estimate the price was built on, so a later variance against
    #: the sliced truth can be judged against what was actually quoted (ADR-0013).
    estimate_source: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    estimated_minutes: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    estimated_grams: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    mesh: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)

    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal(0))

    order: Mapped[Order] = relationship(back_populates="lines")


class OrderEvent(Entity):
    """An append-only record of what happened to an order, and why.

    Kept because "why is this order still in prep?" is a question the farm will ask
    daily, and a status column alone can never answer it.
    """

    __tablename__ = "order_events"
    __table_args__ = (
        # The read path orders by `sequence`, so the index is on `sequence` — the
        # old `(order_id, created_at)` index sorted by a column nothing sorts on.
        #
        # Unique because `sequence` is documented as "the only dependable ordering"
        # and nothing made it dependable: two concurrent writers both counted the
        # existing rows, both got 7, and the history became silently ambiguous. The
        # constraint turns that race into an error one writer retries.
        UniqueConstraint("order_id", "sequence", name="uq_order_events_order_id_sequence"),
        # `SET NULL` on a user delete rewrites matching rows here; unindexed, it
        # scans the whole of order history to do it.
        Index("ix_order_events_actor_id", "actor_id"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    #: 1-based position in this order's history. The only dependable ordering.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    from_status: Mapped[OrderStatus | None] = mapped_column(enum_column(OrderStatus), nullable=True)
    to_status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus), nullable=False)
    #: Machine-readable reason code; clients render it (ADR-0012).
    reason: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    #: Who did it. ``SET NULL`` rather than ``CASCADE``: removing a member of staff
    #: must not delete the history of what they did.
    actor_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)

    order: Mapped[Order] = relationship(back_populates="events")
