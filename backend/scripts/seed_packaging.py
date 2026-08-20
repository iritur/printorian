"""Seed the packing bench: the tara on the shelf and the instruction on the wall.

Unlike the filament catalogue this is not development scenery — it is the data
the post cannot open without. A parcel raised with no active instruction gets no
steps and no norm, and a bench with no boxes has nothing to recommend, so a farm
starting from an empty database needs this run once.

    cd backend && .venv/Scripts/python scripts/seed_packaging.py

Idempotent: tara is keyed by code and updated in place, and the instruction is
skipped when its version is already published — republishing 2.1 with different
steps is exactly what `PackingCatalogue.publish` refuses, because parcels worked
to 2.1 claim a version whose steps have to stay what they were.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from printorian.contexts.packaging import (
    CreatePackStep,
    CreateTara,
    PackingCatalogue,
    PackInstruction,
    PublishInstruction,
    TaraKind,
)
from printorian.core.config import Settings

#: code, name, kind, unit, inner L×W×H mm, price, stock, reorder level.
#:
#: The bag is given a depth of 40 mm although a bag has no third dimension. That
#: is a real measurement of how much a 200 × 150 mailer swallows before it stops
#: closing, and the recommendation needs a number: a bag entered with no depth
#: would be skipped by `recommend` and never suggested for anything.
TARA: list[tuple[str, str, TaraKind, str, tuple[int, int, int] | None, str, str, str]] = [
    ("bag-s", "Пакет S", TaraKind.BAG, "piece", (200, 150, 40), "18", "380", "100"),
    ("box-a", "Коробка A", TaraKind.BOX, "piece", (200, 150, 80), "62", "210", "60"),
    ("box-b", "Коробка B", TaraKind.BOX, "piece", (300, 220, 120), "94", "52", "60"),
    ("box-c", "Коробка C", TaraKind.BOX, "piece", (400, 300, 200), "148", "64", "20"),
    ("wrap-roll", "Пузырчатая плёнка", TaraKind.WRAP, "roll", None, "340", "1", "2"),
    ("filler-kraft", "Наполнитель крафт", TaraKind.FILLER, "kg", None, "120", "31", "10"),
]

#: The instruction as the farm wrote it, norms included. Five steps summing to
#: nine minutes, which is the norm one parcel is measured against.
VERSION = "2.1"

REASON = "Добавлен обязательный слой плёнки после двух повреждений в пути за июль"

STEPS: list[tuple[int, str, str, str | None, str]] = [
    (
        1,
        "Сверить комплектность по списку",
        "Пересчитать по цветам отдельно. Расхождение — остановить и вызвать менеджера.",
        None,
        "2",
    ),
    (
        2,
        "Обернуть каждую деталь пузырчатой плёнкой",
        "Один слой, стык вниз. Детали не должны соприкасаться внутри коробки.",
        "Обязательно для деталей с тонкими стенками: оба повреждения в пути "
        "за последний месяц были именно на непроложенных деталях.",
        "3",
    ),
    (
        3,
        "Уложить в тару, добавить наполнитель",
        "Проверить встряхиванием: содержимое не должно двигаться.",
        None,
        "2",
    ),
    (
        4,
        "Вложить паспорт заказа и чек, заклеить",
        "Документы сверху, лицевой стороной вверх.",
        None,
        "1",
    ),
    (
        5,
        "Взвесить, наклеить этикетку, отсканировать",
        "Скан переводит заказ в статус «Отправлен» и уведомляет заказчика.",
        None,
        "1",
    ),
]


async def main() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        catalogue = PackingCatalogue(session)

        for code, name, kind, unit, dims, price, stock, reorder in TARA:
            await catalogue.stock_tara(
                CreateTara(
                    code=code,
                    name=name,
                    kind=kind,
                    unit=unit,
                    inner_length_mm=Decimal(dims[0]) if dims else None,
                    inner_width_mm=Decimal(dims[1]) if dims else None,
                    inner_height_mm=Decimal(dims[2]) if dims else None,
                    price=Decimal(price),
                    stock=Decimal(stock),
                    reorder_at=Decimal(reorder),
                )
            )
        print(f"tara :: {len(TARA)} positions")

        published = await session.scalar(
            select(PackInstruction).where(PackInstruction.version == VERSION)
        )
        if published is None:
            await catalogue.publish(
                PublishInstruction(
                    version=VERSION,
                    reason=REASON,
                    steps=[
                        CreatePackStep(
                            position=position,
                            title=title,
                            detail=detail,
                            warning=warning,
                            norm_minutes=Decimal(norm),
                        )
                        for position, title, detail, warning, norm in STEPS
                    ],
                )
            )
            print(f"instruction :: {VERSION} published, {len(STEPS)} steps")
        else:
            print(f"instruction :: {VERSION} already published, left alone")

        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
