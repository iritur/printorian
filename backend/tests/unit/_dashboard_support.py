"""Rows the dashboard's read tests are written against.

Shared by the two halves of that suite — the commercial reads and the floor's —
which are separate files because the file-length gate says so and because they
fail for different reasons.

Deliberately thin. These build the minimum row that satisfies the constraints
PostgreSQL actually enforces (ADR-0021), because every one of these tests is
about an aggregate query and none of them is about how an order is created.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from itertools import count

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory.models import MaterialSpec
from printorian.contexts.ordering.models import Order
from printorian.contexts.ordering.policies import OrderStatus
from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus
from printorian.core.ids import EntityId, new_id
from tests.factories import ensure_order, ensure_printer

#: The instant every dashboard read test is taken against. A Tuesday afternoon in
#: the middle of a month and the middle of a quarter, so no window test is
#: accidentally passing because it sits on a boundary.
NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)

#: `orders.number` is unique and 32 characters wide. A counter keeps rows distinct
#: without the tests having to care what a number looks like.
_numbers = count(1)


async def an_order(
    db: AsyncSession,
    *,
    status: OrderStatus,
    created_at: datetime,
    paid_at: datetime | None = None,
    total: Decimal = Decimal(1000),
    sla_credit: Decimal = Decimal(0),
    breakdown: dict[str, object] | None = None,
) -> Order:
    order = Order(
        number=f"ORD-{next(_numbers)}",
        status=status,
        total=total,
        sla_credit=sla_credit,
        paid_at=paid_at,
        price_breakdown=breakdown or {},
    )
    db.add(order)
    await db.flush()
    # `created_at` carries a server default, so it is assigned after the insert
    # rather than in the constructor — otherwise every row lands on now().
    order.created_at = created_at
    await db.flush()
    return order


async def an_order_id(db: AsyncSession) -> EntityId:
    """A minimal order, for jobs that need their foreign key to resolve."""
    order_id = new_id()
    await ensure_order(db, order_id)
    return order_id


async def a_printer(db: AsyncSession, *, name: str) -> EntityId:
    printer_id = new_id()
    await ensure_printer(db, printer_id, name=name)
    return printer_id


async def a_material(db: AsyncSession, *, code: str) -> MaterialSpec:
    spec = MaterialSpec(
        code=code,
        name=code,
        family="PETG",
        color_name="black",
        color_hex="#1b1b1e",
        sell_price_per_gram=Decimal("3.20"),
    )
    db.add(spec)
    await db.flush()
    return spec


def a_job(
    order_id: EntityId,
    *,
    status: JobStatus,
    grams: Decimal,
    printer_id: EntityId | None = None,
    minutes: Decimal = Decimal(60),
    progress: int | None = None,
) -> PrintJob:
    return PrintJob(
        order_id=order_id,
        status=status,
        printer_id=printer_id,
        material_type="PETG-CF-BLACK",
        grams_required=grams,
        estimated_minutes=minutes,
        progress_percent=progress,
    )


__all__ = ["NOW", "a_job", "a_material", "a_printer", "an_order", "an_order_id"]
