"""What lateness owes, and a durable record of every time that figure moved.

The credit itself is one column, `orders.sla_credit`, and the sweep in
`workers/sla.py` overwrote it in place. That was correct arithmetic and no
record at all: the previous value survived nowhere, so "why was this customer
credited 4 200 ₽" and "why did revenue drop between two closes" had no answer
beyond the number currently in the column — on a path where money leaves the
farm through `PaymentsService.refund_sla_credit` and revenue is reported net of
it in `ordering/finance.py`.

**Why this is its own table and not an `order_events` row.** That was the shape
issue #75 proposed, and it is the established one — but `OrderView` eagerly loads
`Order.events` on every read, including `table()`, which loads them for every row
on the page. The credit moves on *every* sweep: at the default
`sla_sweep_seconds=300`, a `standard` promise accrues 0.25 ₽ every five minutes
for the six days it takes to reach the 30% cap, which is 1 728 changes. One page
of twenty such orders would carry thirty-four thousand event rows in a single
response. The ledger is written far more often than an order's history is, and is
read by query rather than by traversal, so it is deliberately *not* reachable from
`Order` — there is no relationship here, and that absence is the point.

The volume is bounded by the cap rather than by how long an order stays late:
once `max_percent` is reached the figure stops moving and nothing more is written.
A farm that finds 1 728 rows per late order too fine-grained should raise
`sla_sweep_seconds`; that setting is the ledger's resolution, which is worth
knowing before it is tuned for some other reason.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from printorian.contexts.ordering.models import Order
from printorian.contexts.ordering.policies import DecayPolicy, policy
from printorian.core.db import Entity, UtcDateTime
from printorian.core.ids import EntityId

#: Recorded on an entry written by the sweep, while the order is still accruing.
CREDIT_ACCRUED = "sla.accrued"
#: Recorded on the entry written when the parcel leaves and the clock stops.
CREDIT_FROZEN_AT_DISPATCH = "sla.frozen_at_dispatch"


class SlaCreditEntry(Entity):
    """One movement of an order's SLA credit, and everything it was derived from.

    Append-only. Nothing updates a row here, and the service never deletes one —
    the only thing that removes an entry is the order itself being deleted, which
    the `CASCADE` below covers.

    The derivation is stored beside the figures rather than left to be looked up,
    because the point of the record is to answer the question years later. The
    terms columns mirror the ones pinned on the order, so an entry says what rule
    was applied even if the order is later re-read through different code.
    """

    __tablename__ = "sla_credit_entries"
    __table_args__ = (
        # The same ordering guarantee `order_events` needed and for the same
        # reason: `created_at` has no sub-second granularity to rely on and the
        # UUIDv7 key only orders to the millisecond, so two entries written in one
        # millisecond sort by their random bits. The constraint also turns a
        # losing race between two sweeps into an integrity error rather than two
        # rows claiming the same position.
        UniqueConstraint("order_id", "sequence", name="uq_sla_credit_entries_order_id_sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("previous >= 0", name="previous_non_negative"),
        CheckConstraint("credit >= 0", name="credit_non_negative"),
        # An entry that records no movement is noise in a ledger whose whole
        # purpose is to say when the figure moved.
        CheckConstraint("credit <> previous", name="credit_actually_moved"),
    )

    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    #: 1-based position in this order's credit history. The only dependable order.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: What the column held before this movement. The half that used to be lost.
    previous: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    #: Machine-readable; clients render it (ADR-0012). `ACCRUED` or
    #: `FROZEN_AT_DISPATCH`.
    reason: Mapped[str] = mapped_column(String(40), nullable=False)

    #: The moment the figure was computed *for*, taken from the injected clock
    #: rather than from the database. `created_at` says when the row was written,
    #: which is the same thing right up until a sweep replays a backlog.
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    #: The promise this was measured against. Copied rather than joined: an order
    #: whose `promised_at` is later corrected must not silently restate what an
    #: earlier entry was derived from.
    promised_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    decay_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    #: The three terms actually applied. Null together, and only for an order
    #: placed before the terms were pinned to it — those still fall back to the
    #: live policy, and an entry says so by leaving these empty rather than by
    #: copying today's values into a row about the past (ADR-0007).
    decay_percent_per_day: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    decay_grace_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decay_max_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)


def terms_for(order: Order) -> DecayPolicy:
    """The terms this order was sold under — not the ones in force today.

    Rebuilt from the columns rather than looked up by code, because the lookup is
    the defect #74 fixed: `POLICIES` holds current values, and reading it here
    made a rate edit reach backwards into promises already sold.

    The fallback covers orders placed before the columns existed. Those keep the
    old behaviour, which is the honest option: their terms were never recorded,
    and inventing a plausible set for them would be a number the farm never
    measured. `ck_orders_decay_terms_all_or_none` is what makes checking one
    column enough to know about all three.
    """
    percent_per_day = order.decay_percent_per_day
    grace_seconds = order.decay_grace_seconds
    max_percent = order.decay_max_percent
    if percent_per_day is None or grace_seconds is None or max_percent is None:
        return policy(order.decay_policy)
    return DecayPolicy(
        code=order.decay_policy,
        percent_per_day=percent_per_day,
        grace=timedelta(seconds=grace_seconds),
        max_percent=max_percent,
    )


def credit_for(order: Order, now: datetime) -> Decimal:
    """What lateness owes on ``order`` at ``now``, in currency units."""
    if order.promised_at is None:
        return Decimal(0)
    percent = terms_for(order).percent_at(promised_at=order.promised_at, now=now)
    return (order.total * percent / Decimal(100)).quantize(Decimal("0.01"))


async def record(
    session: AsyncSession,
    order: Order,
    *,
    previous: Decimal,
    credit: Decimal,
    at: datetime,
    reason: str,
) -> SlaCreditEntry:
    """Append the ledger entry for a movement that has just been decided.

    Added to the session and not flushed: the caller is changing `sla_credit` in
    the same unit of work, and the record of the change belongs in the same
    transaction as the change. Flushing here would let the entry commit while the
    column did not.
    """
    terms = terms_for(order)
    pinned = order.decay_percent_per_day is not None
    entry = SlaCreditEntry(
        order_id=order.id,
        sequence=await next_sequence(session, order.id),
        previous=previous,
        credit=credit,
        reason=reason,
        at=at,
        promised_at=order.promised_at,
        decay_policy=order.decay_policy,
        decay_percent_per_day=terms.percent_per_day if pinned else None,
        decay_grace_seconds=int(terms.grace.total_seconds()) if pinned else None,
        decay_max_percent=terms.max_percent if pinned else None,
    )
    session.add(entry)
    return entry


async def next_sequence(session: AsyncSession, order_id: EntityId) -> int:
    """Next position in this order's credit history.

    ``MAX(sequence) + 1``, answered from the unique index by reading one entry
    rather than counting rows — and `OrderingService._next_sequence` explains why
    the two are not the same once anything has been rolled back.
    """
    highest = await session.scalar(
        select(func.max(SlaCreditEntry.sequence)).where(SlaCreditEntry.order_id == order_id)
    )
    return int(highest or 0) + 1


async def credit_history(session: AsyncSession, order_id: EntityId) -> list[SlaCreditEntry]:
    """This order's credit history, oldest first."""
    rows = await session.scalars(
        select(SlaCreditEntry)
        .where(SlaCreditEntry.order_id == order_id)
        .order_by(SlaCreditEntry.sequence)
    )
    return list(rows)


__all__ = [
    "CREDIT_ACCRUED",
    "CREDIT_FROZEN_AT_DISPATCH",
    "SlaCreditEntry",
    "credit_for",
    "credit_history",
    "next_sequence",
    "record",
    "terms_for",
]
