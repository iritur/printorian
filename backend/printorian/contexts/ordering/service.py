"""Ordering use cases: place, transition, and settle the SLA."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.ordering import events as ordering_events
from printorian.contexts.ordering.models import (
    ORDER_NUMBER_SEQUENCE,
    Order,
    OrderEvent,
    OrderLine,
    RateSnapshotRecord,
)
from printorian.contexts.ordering.policies import OrderStatus, assert_transition, policy
from printorian.contexts.ordering.schemas import (
    OrderTable,
    OrderView,
    PlaceOrder,
    StatusCount,
)
from printorian.contexts.pricing import Breakdown, RateSnapshot, breakdown_to_dict, rates_to_dict
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.pagination import Cursor, after, clamp, newest_first, paginate

_NUMBER_PREFIX = "PR"


class OrderingService:
    """Orders, their pinned prices, and the promise attached to them."""

    def __init__(self, session: AsyncSession, clock: Clock, bus: EventBus) -> None:
        self._db = session
        self._clock = clock
        self._bus = bus

    # -- placing ---------------------------------------------------------

    async def place(
        self,
        data: PlaceOrder,
        breakdown: Breakdown,
        rates: RateSnapshot,
        *,
        customer_id: EntityId | None = None,
    ) -> OrderView:
        """Create an order with ``breakdown`` pinned to it.

        The breakdown is stored verbatim. Nothing downstream reprices the order —
        if the rules change tomorrow, this customer still owes what they agreed to.

        ``rates`` is stored too, and that is new. The order has always carried the
        snapshot *hash*, which proves which rates were used and detects tampering —
        but the values behind the hash lived only in code, so the moment a rate
        changed, every older hash pointed at nothing and ADR-0002's "recompute this
        quote years later" stopped being true. Now the hash resolves.
        """
        now = self._clock.now()
        await self._pin_rates(rates, breakdown.engine_version)
        order = Order(
            number=await self._next_number(),
            status=OrderStatus.DRAFT,
            customer_id=customer_id,
            customer_email=data.customer_email.strip().lower(),
            currency=breakdown.currency.value,
            total=breakdown.total.amount,
            price_breakdown=breakdown_to_dict(breakdown),
            rate_snapshot_id=breakdown.rate_snapshot_id,
            engine_version=breakdown.engine_version,
            promised_at=now + timedelta(days=data.promised_days),
            decay_policy=policy(data.decay_policy).code,
            delivery_method=data.delivery.method,
            delivery_city=data.delivery.city.strip(),
            delivery_postcode=data.delivery.postcode.strip(),
            delivery_address=data.delivery.address.strip(),
            notify_on_progress=data.delivery.notify,
        )

        # The order total is the authority; per-line totals are informational, so
        # they are apportioned rather than recomputed with a second price rule.
        weights = [Decimal(line.quantity) for line in data.lines]
        shares = breakdown.total.allocate(weights) if len(weights) > 1 else [breakdown.total]

        for draft, share in zip(data.lines, shares, strict=True):
            order.lines.append(
                OrderLine(
                    model_name=draft.model_name,
                    material_code=draft.material_code,
                    quantity=draft.quantity,
                    scale=draft.scale,
                    rush=draft.rush,
                    colors=list(draft.colors),
                    finishes=list(draft.finishes),
                    estimate_source="mesh_heuristic",
                    estimated_minutes=draft.estimated_minutes,
                    estimated_grams=draft.estimated_grams,
                    mesh=dict(draft.mesh),
                    line_total=share.amount,
                )
            )

        order.events.append(
            OrderEvent(sequence=1, to_status=OrderStatus.DRAFT, reason="order.placed", details={})
        )
        self._db.add(order)
        await self._db.flush()

        await self._bus.publish(
            ordering_events.OrderPlaced(
                order_id=order.id, number=order.number, total=str(order.total)
            )
        )
        return await self.get(order.id)

    # -- reading ---------------------------------------------------------

    async def get(self, order_id: EntityId) -> OrderView:
        order = await self._db.scalar(
            select(Order)
            .options(selectinload(Order.lines), selectinload(Order.events))
            # Without populate_existing the identity map hands back the instance with
            # its previously-loaded collections, so a freshly written event is simply
            # absent from the result. Reads after a write must be told to refresh.
            .execution_options(populate_existing=True)
            .where(Order.id == order_id)
        )
        if order is None:
            raise NotFoundError("error.ordering.not_found", order_id=str(order_id))
        return OrderView.model_validate(order)

    async def by_number(self, number: str) -> OrderView:
        order = await self._db.scalar(
            select(Order)
            .options(selectinload(Order.lines), selectinload(Order.events))
            .execution_options(populate_existing=True)
            .where(Order.number == number)
        )
        if order is None:
            raise NotFoundError("error.ordering.not_found", number=number)
        return OrderView.model_validate(order)

    async def table(
        self,
        *,
        customer_id: EntityId | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> OrderTable:
        """One page of orders, plus status counts over the whole set.

        This used to return *every* order, with every line and every status event
        eagerly loaded, on every call. Fine at a few hundred; at twenty thousand it
        is the largest response the API can produce and it grows forever.

        The counts are now a ``GROUP BY`` rather than a tally of the rows returned,
        which is both cheaper and — once there is more than one page — the only
        correct answer. Counting the page would have made the chips above the table
        say "12 printing" when it meant "12 printing *on this page*", and the
        scenario's whole reason for those chips is that they describe the table.
        """
        page_size = clamp(limit)
        position = Cursor.decode(cursor) if cursor else None

        query = (
            select(Order)
            .options(selectinload(Order.lines), selectinload(Order.events))
            .execution_options(populate_existing=True)
        )
        if customer_id is not None:
            query = query.where(Order.customer_id == customer_id)
        # Newest-first comes from the UUIDv7 key itself, which sorts by creation
        # time — so this is an index-only seek on the primary key. See
        # `core.pagination` for why the key beats `created_at` here.
        query = newest_first(after(query, position, id_column=Order.id), Order.id, limit=page_size)

        page = paginate(list(await self._db.scalars(query)), page_size)
        rows = [OrderView.model_validate(order) for order in page.rows]

        tally = await self._status_tally(customer_id)
        counts = [StatusCount(status=status, count=tally.get(status, 0)) for status in OrderStatus]
        return OrderTable(
            rows=rows,
            counts=counts,
            total=sum(tally.values()),
            next_cursor=page.next_cursor,
        )

    async def _status_tally(self, customer_id: EntityId | None) -> dict[OrderStatus, int]:
        """How many orders sit in each status, across the whole table.

        One grouped aggregate rather than one query per status: the index on
        ``(status, created_at)`` serves it, and a per-status loop would be fourteen
        round trips to answer one question.
        """
        query = select(Order.status, func.count()).group_by(Order.status)
        if customer_id is not None:
            query = query.where(Order.customer_id == customer_id)
        rows = await self._db.execute(query)
        return dict(rows.tuples().all())

    # -- transitions -----------------------------------------------------

    async def advance(
        self,
        order_id: EntityId,
        target: OrderStatus,
        *,
        reason: str = "",
        actor_id: EntityId | None = None,
    ) -> OrderView:
        """Move an order forward, refusing transitions the machine does not allow."""
        order = await self._db.get(Order, order_id)
        if order is None:
            raise NotFoundError("error.ordering.not_found", order_id=str(order_id))

        assert_transition(order.status, target)
        previous = order.status
        order.status = target

        now = self._clock.now()
        if target is OrderStatus.PAID:
            order.paid_at = now
        elif target is OrderStatus.SHIPPED:
            order.shipped_at = now
            # Freeze the credit at the moment of shipping: the clock stops when the
            # parcel leaves, not when someone gets round to clicking "completed".
            order.sla_credit = self._credit_for(order, now)

        self._db.add(
            OrderEvent(
                order_id=order.id,
                sequence=await self._next_sequence(order.id),
                from_status=previous,
                to_status=target,
                reason=reason or f"order.{target.value}",
                actor_id=actor_id,
            )
        )
        await self._db.flush()

        await self._bus.publish(
            ordering_events.OrderStatusChanged(
                order_id=order.id,
                number=order.number,
                from_status=previous.value,
                to_status=target.value,
            )
        )
        return await self.get(order.id)

    # -- SLA -------------------------------------------------------------

    async def refresh_sla_credit(self, order_id: EntityId) -> OrderView:
        """Recompute what lateness currently owes the customer.

        Called by the worker that watches promises. Recomputed rather than
        accumulated, so a clock correction or a policy fix cannot double-count.
        """
        order = await self._db.get(Order, order_id)
        if order is None:
            raise NotFoundError("error.ordering.not_found", order_id=str(order_id))

        # Once the parcel has left, the credit is settled and must not keep growing.
        # Without this an order sitting in SHIPPED would accrue for ever, and the
        # figure frozen at dispatch would be quietly overwritten by a later sweep.
        if not order.status.counts_against_sla:
            return await self.get(order.id)

        credit = self._credit_for(order, self._clock.now())
        if credit != order.sla_credit:
            order.sla_credit = credit
            await self._db.flush()
            await self._bus.publish(
                ordering_events.SlaCreditAccrued(
                    order_id=order.id, number=order.number, credit=str(credit)
                )
            )
        return await self.get(order.id)

    async def overdue(self) -> list[OrderView]:
        """Orders past their promise and still owed work."""
        now = self._clock.now()
        query = (
            select(Order)
            .options(selectinload(Order.lines), selectinload(Order.events))
            .execution_options(populate_existing=True)
            .where(Order.promised_at.is_not(None), Order.promised_at < now)
        )
        orders = [
            order for order in await self._db.scalars(query) if order.status.counts_against_sla
        ]
        return [OrderView.model_validate(order) for order in orders]

    def _credit_for(self, order: Order, now: object) -> Decimal:
        if order.promised_at is None:
            return Decimal(0)
        percent = policy(order.decay_policy).percent_at(
            promised_at=order.promised_at,
            now=now,  # type: ignore[arg-type]
        )
        return (order.total * percent / Decimal(100)).quantize(Decimal("0.01"))

    # -- internals -------------------------------------------------------

    async def _pin_rates(self, rates: RateSnapshot, engine_version: str) -> None:
        """Make sure the snapshot this quote used exists, exactly once.

        ``ON CONFLICT DO NOTHING`` rather than read-then-write: the id *is* the
        content hash, so two orders priced from identical rates race to insert the
        same row, and the loser has nothing to correct — the row it wanted is
        already there. Checking first would leave a window between the check and the
        insert and turn a normal concurrent checkout into an integrity error.
        """
        values = {
            "id": rates.snapshot_id,
            "payload": rates_to_dict(rates),
            "engine_version": engine_version,
        }
        # One dialect (ADR-0021), so one statement. This used to fork on
        # `dialect.name` because the test suite ran on SQLite and needed its own
        # `INSERT ... ON CONFLICT`.
        statement = postgresql_insert(RateSnapshotRecord).values(**values)
        await self._db.execute(statement.on_conflict_do_nothing(index_elements=["id"]))

    async def _next_sequence(self, order_id: EntityId) -> int:
        """Next position in this order's history.

        ``MAX(sequence) + 1`` rather than ``COUNT(*) + 1``: the two agree only while
        no row has ever been rolled back, and the max is answered from the
        ``(order_id, sequence)`` unique index by reading one entry instead of walking
        every event the order has. The index also makes a losing race an integrity
        error rather than a duplicated position — see `OrderEvent.__table_args__`.
        """
        highest = await self._db.scalar(
            select(func.max(OrderEvent.sequence)).where(OrderEvent.order_id == order_id)
        )
        return int(highest or 0) + 1

    async def _next_number(self) -> str:
        """Sequential, human-quotable order numbers, from the database's sequence.

        See :data:`ORDER_NUMBER_SEQUENCE` for why counting rows was wrong. SQLite —
        the fast test dialect, never production (D1) — has no sequences, so it keeps
        the old count. A collision there would need two concurrent checkouts inside
        one test, and the unique constraint would still catch it.
        """
        if self._db.get_bind().dialect.supports_sequences:
            value = await self._db.scalar(select(ORDER_NUMBER_SEQUENCE.next_value()))
        else:
            value = (await self._db.scalar(select(func.count()).select_from(Order)) or 0) + 1
        return f"{_NUMBER_PREFIX}-{int(value or 1):06d}"
