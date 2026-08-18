"""Seed the journal with the kit's own reports.

The kit's `blog.html` and `blog-post.html` are written as if the farm had been
publishing for months, and the index only makes sense with enough entries to
filter and archive. These are those entries, with report #57 carried across in
full — every heading, callout, listing, quote and table from the kit's article —
so the storefront can be checked against the design rather than against a
placeholder.

    cd backend && .venv/Scripts/python scripts/seed_journal.py

Idempotent: a report whose title is already in the journal is skipped, so
re-running adds nothing and edits nothing somebody has since changed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import printorian.models  # noqa: F401 - registers every table on the metadata
from printorian.contexts.journal import CreatePost, JournalService
from printorian.contexts.journal.models import JournalPost
from printorian.core.clock import SystemClock
from printorian.core.config import get_settings


def report_57() -> list[dict[str, Any]]:
    """«Час печати», the kit's featured article, block for block."""
    return [
        {
            "kind": "figures",
            "title": "Итог отчёта в цифрах",
            "aside": "BAMBU X1C · PETG-CF",
            "rows": [
                {"label": "Амортизация", "value": "41.00 ₽/ч", "tone": "plain"},
                {"label": "Электроэнергия", "value": "1.98 ₽/ч", "tone": "plain"},
                {"label": "Труд · надзор", "value": "45.96 ₽/ч", "tone": "plain"},
                {"label": "Резерв на брак", "value": "10.80 ₽/ч", "tone": "plain"},
                {
                    "label": "Простой · амортизация вхолостую",
                    "value": "6.20 ₽/ч",
                    "tone": "warn",
                },
            ],
            "total_label": "Час печати",
            "total_value": "99.74 ₽",
            "note": "БЕЗ МАТЕРИАЛА · БЕЗ НАКЛАДНЫХ · БЕЗ ПРИБЫЛИ",
        },
        {"kind": "heading", "text": "Что входит в час печати"},
        {
            "kind": "paragraph",
            "text": (
                "Когда заказчик видит цену, он видит одно число. Внутри этого числа — "
                "восемь независимых статей, и только две из них растут пропорционально "
                "размеру модели. Остальные шесть привязаны ко времени, а время "
                "определяется не объёмом, а геометрией: **деталь вдвое меньше "
                "печатается не вдвое быстрее**."
            ),
        },
        {
            "kind": "callout",
            "title": "Правило, из которого всё следует.",
            "text": (
                "Один расчёт цены — чистая детерминированная функция. Никаких "
                "коэффициентов «на глаз»: если цифру нельзя вывести из тарифов, "
                "её не существует."
            ),
            "tone": "live",
        },
        {"kind": "heading", "text": "Материал и продувка"},
        {
            "kind": "paragraph",
            "text": (
                "Материал считается по фактическому объёму сетки, а не по габаритной "
                "коробке: `412.8 г × 2.94 ₽/г = 1 213 ₽`. Герметичность проверяется до "
                "расчёта — на негерметичной сетке цену назвать нельзя, и мы честно об "
                "этом говорим."
            ),
        },
        {
            "kind": "paragraph",
            "text": (
                "Вторая статья почти всегда становится сюрпризом. При многоцветной "
                "печати каждая смена цвета требует прочистить сопло, и этот материал "
                "уходит в отход. На двухцветной модели это **62 грамма из 475** — 13% "
                "материала, которые никогда не попадут в деталь."
            ),
        },
        {"kind": "heading", "text": "Электроэнергия"},
        {
            "kind": "paragraph",
            "text": (
                "Самая переоценённая статья. X1C с подогревом стола и камеры потребляет "
                "в среднем 0.32 кВт·ч — за 23.5 часа печати это 9.4 кВт·ч, или **58 "
                "рублей на весь заказ**. Меньше одного процента итоговой цены. Мы всё "
                "равно показываем её отдельной строкой: строка, которая обнуляет чей-то "
                "аргумент, стоит того, чтобы её печатать."
            ),
        },
        {
            "kind": "code",
            "label": "PRICING/ENGINE.PY",
            "note": "ФРАГМЕНТ",
            "code": (
                "def electricity(hours: Decimal, draw_kw: Decimal, tariff: Money) -> Money:\n"
                '    """Потребление за время печати. Простой считается отдельно —\n'
                '    выключенный принтер не должен попадать в цену чужого заказа."""\n'
                "    return tariff * (hours * draw_kw)"
            ),
        },
        {"kind": "heading", "text": "Амортизация"},
        {
            "kind": "paragraph",
            "text": (
                "Принтер за 142 000 ₽ с ресурсом 10 000 часов даёт 14.20 ₽/ч «голой» "
                "амортизации. Но ресурс — не то же самое, что срок службы: сопло, ремни "
                "и подшипники меняются по наработке, и это тоже амортизация. С учётом "
                "расходников получается **41.00 ₽/ч**."
            ),
        },
        {
            "kind": "quote",
            "text": (
                "Простаивающий принтер стоит 149 рублей в сутки. Двенадцать "
                "простаивающих — почти 54 тысячи в год."
            ),
            "cite": "ОТЧЁТ #52 · КАРТА ОБСЛУЖИВАНИЯ",
        },
        {"kind": "heading", "text": "Труд"},
        {
            "kind": "list",
            "items": [
                "**Подготовка задания** — фиксированные 0.5 часа на заказ, независимо "
                "от количества. Раскладка на столе, проверка ориентации, настройка "
                "поддержек.",
                "**Надзор за печатью** — пропорционально времени, но с коэффициентом "
                "0.05: оператор не стоит у машины, он обходит двенадцать.",
            ],
        },
        {"kind": "heading", "text": "Резерв на брак"},
        {
            "kind": "table",
            "head": ["Класс риска", "Признак", "Доля брака", "Резерв"],
            "rows": [
                ["A", "Компактная, широкое основание", "2.4%", "3%"],
                ["B", "Стенки от 0.8 до 1.2 мм", "6.1%", "6%"],
                ["C", "Высота / основание больше 4", "11.8%", "12%"],
            ],
            "align": ["start", "start", "end", "end"],
        },
        {
            "kind": "callout",
            "title": "Чего мы не делаем.",
            "text": (
                "Резерв не перекладывается на конкретного заказчика, чья печать "
                "сорвалась. Перепечать за наш счёт — на то он и резерв."
            ),
            "tone": "plain",
        },
        {"kind": "heading", "text": "Почему это видит заказчик"},
        {
            "kind": "paragraph",
            "text": (
                "Стандартный контраргумент: показывая себестоимость, вы приглашаете "
                "торговаться. За четыре месяца прозрачной сметы мы получили обратное. "
                "Споров о цене стало **меньше**, потому что спорить теперь можно только "
                "с конкретной строкой — а конкретная строка либо обоснована тарифом, "
                "либо нет."
            ),
        },
    ]


#: The kit's own back catalogue, oldest first so the numbering matches its screens.
SEEDS: list[dict[str, Any]] = [
    {
        "title": "Амортизация принтера: 41 рубль в час и откуда взялась эта цифра",
        "section": "fleet",
        "excerpt": "Ресурс машины, расходники и то, что обычно забывают включить в час.",
    },
    {
        "title": "Скидка за просрочку начисляется сама. И это дешевле, чем спорить",
        "section": "architecture",
        "excerpt": "Опубликованное правило вместо разговора с менеджером.",
    },
    {
        "title": "Тонкие стенки: как мы предупреждаем до оплаты, а не после печати",
        "section": "architecture",
        "excerpt": "Разбор сетки на загрузке и почему замечание дешевле перепечати.",
    },
    {
        "title": "Продувка при смене цвета: 62 грамма, которые никто не считает",
        "section": "materials",
        "excerpt": "13% материала, которые никогда не попадут в деталь.",
    },
    {
        "title": "Почему мы отказались от отдельной базы для витрины",
        "section": "architecture",
        "excerpt": "Одна база, один источник правды и цена, которую за это платим.",
    },
    {
        "title": "Шлифовка, грунтовка, окраска: где заканчивается автоматика",
        "section": "postprocessing",
        "excerpt": "Постобработка — единственный ручной этап. Считаем нормо-часы.",
    },
    {
        "title": "Карта обслуживания: 3 040 часов на одной машине",
        "section": "fleet",
        "excerpt": "Периодичность операций и почему сопло меняют по наработке.",
    },
    {
        "title": "Планировщик: как заказ находит свободный принтер",
        "section": "fleet",
        "excerpt": "Совместимость, приоритет срочных заказов и лист ожидания.",
    },
    {
        "title": "Драйвер никогда не выдумывает данные",
        "section": "architecture",
        "excerpt": "Отключённый принтер сообщает «не в сети» и поднимает тревогу.",
    },
    {
        "title": "PETG-CF: когда углеволокно оправдано, а когда это переплата",
        "section": "materials",
        "excerpt": "Прочность и УФ-стойкость против цены сопла и абразивного износа.",
    },
    {
        "title": "Скидка за объём: где ставить пороги, чтобы не потерять маржу",
        "section": "cost",
        "excerpt": "Ступенчатая скидка создаёт обрыв: на 9 штуках дороже, чем на 10.",
    },
    {
        "title": "Час печати",
        "section": "cost",
        "lede": (
            "Сколько на самом деле стоит час работы принтера — и почему мы показываем "
            "эту цифру заказчику до оплаты, а не прячем её в итоговой сумме."
        ),
        "excerpt": (
            "Разбираем полную структуру себестоимости: материал, продувка при смене "
            "цвета, электроэнергия, амортизация, труд, брак."
        ),
        "data_note": "12 принтеров · 90 суток",
        "blocks": report_57(),
    },
]

AUTHOR = "Инженерная группа · ферма KN-SOL.21"

#: The journal comes out weekly, and the seeded dates say so.
#:
#: Not cosmetic. The index measures its own cadence from the real gaps between
#: publications («ВЫХОДИТ 1 / НЕД»), so seeding everything at once would leave
#: twelve reports with no rhythm to measure and the figure absent. Spacing them a
#: week apart — which is also what the kit's archive shows — makes the stat true
#: rather than decorative.
CADENCE = timedelta(days=7)


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    written = 0
    # Oldest first, landing the newest on today, so the archive reads the way the
    # kit's does — most recent at the top, a week between each.
    first_at = datetime.now(UTC) - CADENCE * (len(SEEDS) - 1)

    async with factory() as session:
        service = JournalService(session, SystemClock())
        for index, seed in enumerate(SEEDS):
            existing = await session.scalar(
                select(JournalPost).where(JournalPost.title == seed["title"])
            )
            if existing is not None:
                continue
            created = await service.create(
                CreatePost(
                    title=str(seed["title"]),
                    section=seed["section"],
                    lede=str(seed.get("lede", "")),
                    excerpt=str(seed.get("excerpt", "")),
                    author=AUTHOR,
                    data_note=str(seed.get("data_note", "")),
                    blocks=seed.get("blocks", _stub(str(seed["title"]))),
                    is_published=True,
                )
            )
            # Backdated after the fact: `create` stamps "now", which is right for a
            # report somebody actually writes and wrong for a back catalogue.
            post = await session.scalar(select(JournalPost).where(JournalPost.slug == created.slug))
            if post is not None:
                post.published_at = first_at + CADENCE * index
                await session.commit()
            written += 1

    await engine.dispose()
    # ASCII only: the Windows console this runs on is cp1251 and a Cyrillic
    # print here is a UnicodeEncodeError at the end of a successful seed.
    print(f"journal seeded: {written} new, {len(SEEDS) - written} already there")


def _stub(title: str) -> list[dict[str, Any]]:
    """A short body for the archive entries.

    The kit only writes one article in full; the rest exist to fill the index and
    the archive. They get a real heading and paragraph rather than lorem ipsum, so
    a reader who opens one finds a page that makes sense.
    """
    return [
        {"kind": "heading", "text": "Коротко"},
        {
            "kind": "paragraph",
            "text": (
                f"«{title}» — разбор одного решения фермы с расчётом, на котором оно "
                "основано. Полный текст отчёта готовится."
            ),
        },
    ]


if __name__ == "__main__":
    asyncio.run(main())
