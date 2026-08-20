"""Choosing a box, and reading the tara table.

The recommendation is the load-bearing part. Under time pressure a packer reaches
for the size they can see, and the cost of reaching one size too large is invisible
per parcel and obvious per quarter: filler, volumetric weight, and a shipping line
in the estimate that no longer matches what left the building. Naming the box the
geometry implies — beside, not instead of, the packer's own judgement — makes the
cheap choice the easy one.

It is only a recommendation. `PackTask.tara_id` records what was actually used, and
the gap between the two is reported as an accuracy percentage rather than enforced.
A rule that refused the packer's choice would be overridden within a week by people
putting the wrong code in, and the farm would lose the measurement as well as the
argument.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.packaging.models import PackTask, PackUse, Tara
from printorian.contexts.packaging.policies import (
    ENCLOSURES,
    STATS_DAYS,
    Dims,
    PackStatus,
    fits,
)
from printorian.contexts.packaging.schemas import TaraRow
from printorian.core.ids import EntityId


async def enclosures(db: AsyncSession) -> list[Tara]:
    """Every active box and bag, cheapest first — the order `recommend` relies on."""
    return list(
        await db.scalars(
            select(Tara)
            .where(Tara.is_active.is_(True), Tara.kind.in_(ENCLOSURES))
            .order_by(Tara.price, Tara.code)
        )
    )


def recommend(candidates: list[Tara], dims: Dims) -> Tara | None:
    """The cheapest thing the batch fits in.

    Cheapest rather than tightest, and the two are not the same: a snug box that
    costs more than a roomy one is a worse answer to a question that is about
    money. Where prices tie, `enclosures` orders by code, so the same batch always
    gets the same recommendation — a suggestion that moved between refreshes would
    teach packers to ignore it.

    ``None`` when nothing in the catalogue fits, which is a real answer: it means
    somebody has to make a decision the system cannot, and the screen says so
    rather than naming the largest box and hoping.

    Also ``None`` when the batch has no measured size at all. An order whose lines
    were priced from an estimate rather than from geometry arrives here as a
    zero-volume box, and a zero fits inside everything — so the naive answer is
    the cheapest bag in the catalogue, offered with total confidence, for a parcel
    nobody has measured. Silence is the honest output, and the screen already
    knows how to say it.
    """
    if dims.volume_cm3 <= 0:
        return None
    for tara in candidates:
        inner = _inner_of(tara)
        if inner is not None and fits(inner, dims):
            return tara
    return None


def _inner_of(tara: Tara) -> Dims | None:
    """A box's inside, or ``None`` for one nobody measured."""
    if tara.inner_length_mm is None or tara.inner_width_mm is None or tara.inner_height_mm is None:
        return None
    return Dims(tara.inner_length_mm, tara.inner_width_mm, tara.inner_height_mm)


async def tara_rows(db: AsyncSession, *, now: datetime, days: int = STATS_DAYS) -> list[TaraRow]:
    """The table: price, stock, what it goes at, and how long that lasts.

    The consumption rate is summed from the ledger rather than kept as a counter,
    so "хватит на 1.3 мес" is a statement about what the post actually did and not
    about what somebody typed into a field once.
    """
    since = now - timedelta(days=days)
    consumed = (
        await db.execute(
            select(PackUse.tara_id, func.coalesce(func.sum(PackUse.quantity), 0))
            .join(PackTask, PackTask.id == PackUse.task_id)
            .where(PackTask.finished_at.is_not(None), PackTask.finished_at >= since)
            .group_by(PackUse.tara_id)
        )
    ).all()
    used: dict[EntityId, Decimal] = {
        tara_id: Decimal(str(quantity or 0)) for tara_id, quantity in consumed
    }
    stocked = list(
        await db.scalars(
            select(Tara).where(Tara.is_active.is_(True)).order_by(Tara.kind, Tara.price, Tara.code)
        )
    )

    rows: list[TaraRow] = []
    for tara in stocked:
        # Normalised to a month whatever the window is, so the column keeps its
        # header's meaning if `STATS_DAYS` is ever changed.
        rate = used.get(tara.id, Decimal(0)) * Decimal(30) / Decimal(max(1, days))
        row = TaraRow.model_validate(tara)
        row.used_per_month = rate.quantize(Decimal("0.1"))
        row.months_left = (tara.stock / rate).quantize(Decimal("0.1")) if rate > 0 else None
        rows.append(row)
    return rows


async def tara_accuracy(
    db: AsyncSession, *, now: datetime, days: int = STATS_DAYS
) -> Decimal | None:
    """How often the recommended box was the box used, over the window.

    The honest gauge on `policies.stack_box`, which over-estimates on purpose. A
    figure that sits at 96% says the crude rule is good enough; one that drifts
    down says the farm has started printing things it does not describe well, and
    that is the signal to replace the rule with a measured one.

    ``None`` when nothing has shipped — an accuracy over no parcels is not 100%.
    """
    since = now - timedelta(days=days)
    packed = list(
        await db.scalars(
            select(PackTask).where(
                PackTask.status.in_({PackStatus.READY, PackStatus.SHIPPED}),
                PackTask.finished_at.is_not(None),
                PackTask.finished_at >= since,
                PackTask.tara_id.is_not(None),
            )
        )
    )
    if not packed:
        return None

    candidates = await enclosures(db)
    agreed = 0
    for task in packed:
        wanted = recommend(candidates, Dims(task.length_mm, task.width_mm, task.height_mm))
        if wanted is not None and wanted.id == task.tara_id:
            agreed += 1
    return (Decimal(agreed) / Decimal(len(packed)) * 100).quantize(Decimal("0.1"))


__all__ = ["enclosures", "recommend", "tara_accuracy", "tara_rows"]
