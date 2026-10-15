"""What went into a parcel: the box, the extras, and the stock they came off.

`choose_tara`, split out of `service.py` when moving its consumption onto the
shared stock (#35) took that file past the 400-line gate — and split *here*
because this is the one part of a parcel's life that touches another context's
table. The shape is `payments.refunding`: a mixin, so callers keep one object.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from printorian.contexts.inventory import ArticleKind, StockArticles
from printorian.contexts.packaging.models import PackUse, Tara
from printorian.contexts.packaging.schemas import ChooseTara, PackView
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from printorian.contexts.packaging.models import PackTask
    from printorian.core.clock import Clock


class Choosing:
    """`choose_tara`, for `PackagingService` to inherit.

    The attributes are declared, not set: `PackagingService.__init__` supplies
    them, and annotating them here lets `mypy --strict` check this half alone.
    """

    _db: AsyncSession
    _clock: Clock
    _task: Callable[[EntityId], Awaitable[PackTask]]
    _view: Callable[[PackTask], Awaitable[PackView]]

    async def choose_tara(self, task_id: EntityId, data: ChooseTara) -> PackView:
        """Record what went into the parcel, and what it cost.

        Written when the packer names it rather than when the parcel closes: a
        parcel abandoned halfway has still consumed the box, and a ledger that
        only counted finished work would understate the month.
        """
        task = await self._task(task_id)
        if task.status.is_terminal:
            raise DomainRuleViolationError("error.packaging.closed", status=task.status.value)

        wanted: dict[EntityId, Decimal] = {data.tara_id: Decimal(1)}
        for tara_id, quantity in data.extras.items():
            if quantity > 0:
                wanted[tara_id] = wanted.get(tara_id, Decimal(0)) + quantity

        # Replaced wholesale rather than added to: choosing a different box is a
        # correction, and a ledger that accumulated both would bill the parcel for
        # two enclosures it never had.
        #
        # Issued as a statement rather than by walking `task.used`. The rows are
        # added with `session.add` rather than through the relationship, so an
        # already-loaded collection does not see them — and a second call would
        # find nothing to delete and leave the parcel billed for both boxes. Found
        # by the test that changes its mind about the box.
        #
        # What the parcel was using goes **back on the shelf** first. The old code
        # deleted these rows and decremented again for the new choice, so a packer
        # who changed their mind about the box took two out of stock for one
        # parcel. Read before the delete, because the rows are the only record of
        # what was taken (#35).
        stock = StockArticles(self._db)
        now = self._clock.now()
        previous = (
            await self._db.execute(
                select(Tara, PackUse.quantity)
                .join(PackUse, PackUse.tara_id == Tara.id)
                .where(PackUse.task_id == task.id)
            )
        ).all()
        for used_tara, used_quantity in previous:
            await stock.give_back(
                ArticleKind.PACKAGING, used_tara.code, used_quantity, at=now, reference=task.number
            )
            await _mirror(stock, used_tara)
        await self._db.execute(delete(PackUse).where(PackUse.task_id == task.id))
        await self._db.flush()

        cost = Decimal(0)
        for tara_id, quantity in wanted.items():
            tara = await self._db.get(Tara, tara_id)
            if tara is None:
                raise NotFoundError("error.packaging.tara_not_found")
            self._db.add(
                PackUse(task_id=task.id, tara_id=tara.id, quantity=quantity, unit_price=tara.price)
            )
            # Stock moves here too. The alternative — decrementing on close — has
            # the post's shelf disagreeing with the post's screen for the length
            # of every parcel, which is when somebody is looking at both. Through
            # the shared stock now (#35), so the store's ledger shows the box
            # leaving under the parcel's number.
            await stock.consume(
                ArticleKind.PACKAGING, tara.code, quantity, at=now, reference=task.number
            )
            await _mirror(stock, tara)
            cost += tara.price * quantity

        task.tara_id = data.tara_id
        task.packaging_cost = cost.quantize(Decimal("0.01"))
        await self._db.flush()
        return await self._view(task)


async def _mirror(stock: StockArticles, tara: Tara) -> None:
    """Keep `packaging_tara.stock` equal to the article while both exist.

    Expand/contract (INFRASTRUCTURE §4.5): the article is the truth, and this
    column is written beside it only so that rolling the application back does
    not need the database rolled back too. Nothing in this release reads it; the
    migration that drops it ends this function.
    """
    on_hand = await stock.on_hand(ArticleKind.PACKAGING, [tara.code])
    tara.stock = on_hand.get(tara.code, tara.stock)


__all__ = ["Choosing"]
