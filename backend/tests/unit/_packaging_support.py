"""The bench a packing test needs before it can assert anything.

Shared rather than repeated, because every one of these is a real row: the parcel
table has foreign keys into `orders` and `users`, and the recommendation is read
from an actual catalogue. A test that faked any of it would be testing a fake.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from itertools import count

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.packaging import (
    CreatePackStep,
    CreatePackTask,
    CreateTara,
    Dims,
    PackagingService,
    PackingCatalogue,
    PublishInstruction,
    TaraKind,
)
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from tests.factories import ensure_order, ensure_user

#: Keeps order numbers distinct; `orders.number` is unique.
_orders = count(1)

#: Keeps packer emails distinct; `users.email` is unique.
_packers = count(1)


def mm(length: int, width: int, height: int) -> Dims:
    return Dims(Decimal(length), Decimal(width), Decimal(height))


async def an_order(db: AsyncSession):
    """A real order row: `packaging_tasks.order_id` is a real foreign key."""
    order_id = new_id()
    await ensure_order(db, order_id, number=f"ORD-P{next(_orders)}")
    return order_id


async def a_packer(db: AsyncSession):
    packer_id = new_id()
    await ensure_user(db, packer_id, email=f"packer-{next(_packers)}@printorian.test")
    return packer_id


async def a_shelf(db: AsyncSession) -> None:
    """Two boxes and a roll of film, priced so cheapest and smallest disagree."""
    catalogue = PackingCatalogue(db)
    await catalogue.stock_tara(
        CreateTara(
            code="box-a",
            name="Коробка A",
            kind=TaraKind.BOX,
            inner_length_mm=Decimal(200),
            inner_width_mm=Decimal(150),
            inner_height_mm=Decimal(80),
            price=Decimal(62),
            stock=Decimal(10),
        )
    )
    await catalogue.stock_tara(
        CreateTara(
            code="box-b",
            name="Коробка B",
            kind=TaraKind.BOX,
            inner_length_mm=Decimal(300),
            inner_width_mm=Decimal(220),
            inner_height_mm=Decimal(120),
            price=Decimal(94),
            stock=Decimal(10),
        )
    )
    await catalogue.stock_tara(
        CreateTara(
            code="wrap-roll",
            name="Плёнка",
            kind=TaraKind.WRAP,
            unit="roll",
            price=Decimal(340),
            stock=Decimal(4),
        )
    )


async def an_instruction(db: AsyncSession) -> None:
    await PackingCatalogue(db).publish(
        PublishInstruction(
            version="2.1",
            reason="Добавлен слой плёнки после двух повреждений",
            steps=[
                CreatePackStep(position=1, title="Сверить комплектность", norm_minutes=Decimal(2)),
                CreatePackStep(
                    position=2,
                    title="Обернуть плёнкой",
                    warning="Стенки 0.6 мм — оба повреждения были на непроложенных деталях.",
                    norm_minutes=Decimal(3),
                ),
                CreatePackStep(position=3, title="Уложить и заклеить", norm_minutes=Decimal(3)),
                CreatePackStep(
                    position=4, title="Взвесить и промаркировать", norm_minutes=Decimal(1)
                ),
            ],
        )
    )


async def a_parcel(db: AsyncSession, clock: FixedClock, *, cutoff: datetime | None = None):
    await a_shelf(db)
    await an_instruction(db)
    service = PackagingService(db, clock, EventBus())
    return service, await service.raise_parcel(
        CreatePackTask(
            order_id=await an_order(db),
            delivery_method="courier",
            cutoff_at=cutoff,
            items=10,
            estimated_grams=Decimal(1040),
            length_mm=Decimal(190),
            width_mm=Decimal(140),
            height_mm=Decimal(70),
            wrap_required=True,
        )
    )


__all__ = [
    "a_packer",
    "a_parcel",
    "a_shelf",
    "an_instruction",
    "an_order",
    "mm",
]
