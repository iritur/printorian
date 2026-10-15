"""Moving the non-filament stock: every change to `on_hand` is a ledger row.

The rule this module exists to keep is the one the two catalogues it replaces
broke: **quantity is never restated, only moved.** `PackingCatalogue.stock_tara`
used to overwrite `packaging_tara.stock` with whatever was typed, so "how did we
get from 40 boxes to 12" had no answer. Here a receipt adds, an issue or a
write-off takes away, a correction from a count says by how much and which way,
and each writes an `ArticleMovement` in the same unit of work as the change.

Every method takes ``at`` rather than holding a clock, the convention
`placement.PlacementService` keeps, so a context that consumes stock — packing
a parcel — does not need a clock of its own to do it.

**Consumption clamps, deliberate removal refuses.** A packer who takes a box the
book says is not there has still used it; refusing the parcel over a stale count
would block the post, so `consume` records what was taken and leaves `on_hand`
at zero, and the next stocktake finds the difference. A person writing off or
issuing more than the book holds is a mistake on a form, and is refused before
anything changes, as for a spool.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory.article_models import (
    MOVED_RETURNED,
    ArticleMovement,
    StockArticle,
)
from printorian.contexts.inventory.article_schemas import (
    ArticleView,
    PlaceArticle,
    RegisterArticle,
    TakeArticle,
)
from printorian.contexts.inventory.models import StorageCell
from printorian.contexts.inventory.movements import (
    MOVED_COUNTED,
    MOVED_ISSUED,
    MOVED_MOVED,
    MOVED_RECEIVED,
    MOVED_WRITTEN_OFF,
)
from printorian.contexts.inventory.placement import occupants
from printorian.contexts.inventory.policies import ArticleKind
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId


class StockArticles:
    """The non-filament half of the store."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # -- the catalogue -----------------------------------------------------

    async def register(self, data: RegisterArticle) -> ArticleView:
        """Declare an item, or rename it and move its reorder line. Never its count."""
        article = await self._find(data.kind, data.code)
        if article is None:
            article = StockArticle(kind=data.kind, code=data.code, on_hand=Decimal(0))
            self._db.add(article)
        article.name = data.name
        article.unit = data.unit
        article.reorder_at = data.reorder_at
        article.is_active = True
        await self._db.flush()
        return await self.view(article.id)

    async def rows(self, kind: ArticleKind | None = None) -> list[ArticleView]:
        query = select(StockArticle, StorageCell.address).outerjoin(
            StorageCell, StorageCell.id == StockArticle.cell_id
        )
        query = query.where(StockArticle.is_active.is_(True))
        if kind is not None:
            query = query.where(StockArticle.kind == kind)
        rows = await self._db.execute(query.order_by(StockArticle.kind, StockArticle.code))
        return [_view(article, address) for article, address in rows.all()]

    async def view(self, article_id: EntityId) -> ArticleView:
        row = (
            await self._db.execute(
                select(StockArticle, StorageCell.address)
                .outerjoin(StorageCell, StorageCell.id == StockArticle.cell_id)
                .where(StockArticle.id == article_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("error.inventory.article_not_found", article_id=str(article_id))
        article, address = row
        return _view(article, address)

    async def on_hand(self, kind: ArticleKind, codes: Iterable[str]) -> dict[str, Decimal]:
        """How many of each, for a context that keeps the rest of the catalogue."""
        wanted = list(codes)
        if not wanted:
            return {}
        rows = await self._db.execute(
            select(StockArticle.code, StockArticle.on_hand).where(
                StockArticle.kind == kind, StockArticle.code.in_(wanted)
            )
        )
        return {str(row[0]): Decimal(row[1]) for row in rows.all()}

    # -- coming in ---------------------------------------------------------

    async def receive(
        self,
        kind: ArticleKind,
        code: str,
        *,
        quantity: Decimal,
        at: datetime,
        name: str = "",
        unit: str = "piece",
        unit_cost: Decimal | None = None,
        reference: str | None = None,
        actor_id: EntityId | None = None,
    ) -> StockArticle:
        """A delivery counted in. An item the farm has never stocked is declared by it.

        ``unit_cost`` moves only when the receipt recorded one: a delivery counted
        before its invoice leaves the last known cost standing rather than
        replacing it with nothing.
        """
        article = await self._ensure(kind, code, name=name, unit=unit)
        article.on_hand += quantity
        if unit_cost is not None:
            article.unit_cost = unit_cost
        await self._record(article, MOVED_RECEIVED, quantity, at, actor_id, reference=reference)
        return article

    async def give_back(
        self,
        kind: ArticleKind,
        code: str,
        quantity: Decimal,
        *,
        at: datetime,
        reference: str | None = None,
        actor_id: EntityId | None = None,
    ) -> None:
        """Put back what a correction un-used — the box a packer changed their mind about."""
        article = await self._ensure(kind, code)
        article.on_hand += quantity
        await self._record(article, MOVED_RETURNED, quantity, at, actor_id, reference=reference)

    # -- going out ---------------------------------------------------------

    async def consume(
        self,
        kind: ArticleKind,
        code: str,
        quantity: Decimal,
        *,
        at: datetime,
        reference: str | None = None,
        actor_id: EntityId | None = None,
    ) -> None:
        """Production used some. Clamped at zero — see the module docstring."""
        article = await self._ensure(kind, code)
        article.on_hand = max(Decimal(0), article.on_hand - quantity)
        await self._record(article, MOVED_ISSUED, quantity, at, actor_id, reference=reference)

    async def issue(
        self, article_id: EntityId, data: TakeArticle, *, at: datetime, actor_id: EntityId | None
    ) -> ArticleView:
        """Handed to the floor by a person — a nozzle to a printer, a can to the post."""
        return await self._take(article_id, data, MOVED_ISSUED, at=at, actor_id=actor_id)

    async def write_off(
        self, article_id: EntityId, data: TakeArticle, *, at: datetime, actor_id: EntityId | None
    ) -> ArticleView:
        """Gone for good — damaged, expired, lost."""
        return await self._take(article_id, data, MOVED_WRITTEN_OFF, at=at, actor_id=actor_id)

    async def restate(
        self,
        kind: ArticleKind,
        code: str,
        on_hand: Decimal,
        *,
        at: datetime,
        actor_id: EntityId | None = None,
        note: str | None = None,
    ) -> None:
        """Correct the book to what somebody counted, recording by how much.

        What the old catalogues' absolute writes become. A count that agrees with
        the book writes nothing: a ledger of non-movements is noise.
        """
        article = await self._ensure(kind, code)
        difference = on_hand - article.on_hand
        if difference == 0:
            return
        article.on_hand = on_hand
        await self._record(article, MOVED_COUNTED, abs(difference), at, actor_id, note=note)

    # -- where it is -------------------------------------------------------

    async def place(
        self, article_id: EntityId, data: PlaceArticle, *, at: datetime, actor_id: EntityId | None
    ) -> ArticleView:
        article = await self._article(article_id)
        cell = await self._db.scalar(select(StorageCell).where(StorageCell.address == data.address))
        if cell is None:
            raise NotFoundError("error.inventory.cell_not_found", address=data.address)
        was = await self._address(article.cell_id)
        if was == cell.address:
            raise DomainRuleViolationError(
                "error.inventory.movement_not_a_move", address=data.address
            )
        # The same refusal a spool gets, from the same count: a declared capacity
        # is a number of things, and an article is one (`placement.occupants`).
        occupied = await occupants(self._db, cell.id)
        if cell.capacity_lots is not None and occupied >= cell.capacity_lots:
            raise DomainRuleViolationError(
                "error.inventory.cell_full",
                address=cell.address,
                capacity_lots=cell.capacity_lots,
                occupied=occupied,
            )
        article.cell_id = cell.id
        await self._record(
            article,
            MOVED_MOVED,
            Decimal(0),
            at,
            actor_id,
            from_address=was,
            to_address=cell.address,
            note=data.note,
        )
        return await self.view(article.id)

    # -- internals ---------------------------------------------------------

    async def _take(
        self,
        article_id: EntityId,
        data: TakeArticle,
        reason: str,
        *,
        at: datetime,
        actor_id: EntityId | None,
    ) -> ArticleView:
        article = await self._article(article_id)
        # Before anything changes: a refusal after the write is not a refusal.
        if data.quantity > article.on_hand:
            raise DomainRuleViolationError(
                "error.inventory.take_exceeds_on_hand",
                requested=str(data.quantity),
                on_hand=str(article.on_hand),
            )
        article.on_hand -= data.quantity
        await self._record(
            article,
            reason,
            data.quantity,
            at,
            actor_id,
            from_address=await self._address(article.cell_id),
            reference=data.reference,
            note=data.note,
        )
        return await self.view(article.id)

    async def _record(
        self,
        article: StockArticle,
        reason: str,
        quantity: Decimal,
        at: datetime,
        actor_id: EntityId | None,
        *,
        from_address: str | None = None,
        to_address: str | None = None,
        reference: str | None = None,
        note: str | None = None,
    ) -> None:
        # Flushed first, then flushed again: `MAX(sequence) + 1` reads the ledger,
        # and two rows added before one flush would claim the same rung (the trap
        # `placement.record_movement` left in HANDOFF).
        await self._db.flush()
        highest = await self._db.scalar(
            select(func.max(ArticleMovement.sequence)).where(
                ArticleMovement.article_id == article.id
            )
        )
        self._db.add(
            ArticleMovement(
                article_id=article.id,
                sequence=int(highest or 0) + 1,
                reason=reason,
                quantity=quantity,
                on_hand_after=article.on_hand,
                at=at,
                actor_id=actor_id,
                from_address=from_address,
                to_address=to_address,
                reference=reference,
                note=note,
            )
        )
        await self._db.flush()

    async def _find(self, kind: ArticleKind, code: str) -> StockArticle | None:
        return await self._db.scalar(
            select(StockArticle).where(StockArticle.kind == kind, StockArticle.code == code)
        )

    async def _ensure(
        self, kind: ArticleKind, code: str, *, name: str = "", unit: str = "piece"
    ) -> StockArticle:
        article = await self._find(kind, code)
        if article is None:
            article = StockArticle(
                kind=kind, code=code, name=name or code, unit=unit, on_hand=Decimal(0)
            )
            self._db.add(article)
            await self._db.flush()
        return article

    async def _article(self, article_id: EntityId) -> StockArticle:
        article = await self._db.get(StockArticle, article_id)
        if article is None:
            raise NotFoundError("error.inventory.article_not_found", article_id=str(article_id))
        return article

    async def _address(self, cell_id: EntityId | None) -> str | None:
        if cell_id is None:
            return None
        return await self._db.scalar(select(StorageCell.address).where(StorageCell.id == cell_id))


def _view(article: StockArticle, address: str | None) -> ArticleView:
    view = ArticleView.model_validate(article)
    view.cell = address
    return view


__all__ = ["StockArticles"]
