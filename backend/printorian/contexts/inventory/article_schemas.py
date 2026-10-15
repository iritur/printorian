"""DTOs for the non-filament stock (`article_models`).

No money here. `StockArticle.unit_cost` is a purchase price, and these views are
read by the store screen under `VIEW_PRODUCTION`; the dead-stock figure that
costs an article is served apart, behind `VIEW_FINANCIALS`, as it is for spools.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.inventory.policies import ArticleKind
from printorian.core.ids import EntityId


class RegisterArticle(BaseModel):
    """Declare a stocked item, or update what it is called and when to reorder.

    No quantity: how many there are is only ever changed by a movement — a
    receipt, an issue, a write-off, a count — so the ledger can say why.
    """

    kind: ArticleKind
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=200)
    unit: str = Field(default="piece", min_length=1, max_length=20)
    reorder_at: Decimal = Field(default=Decimal(0), ge=0)


class ArticleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    kind: ArticleKind
    code: str
    name: str
    unit: str
    on_hand: Decimal
    reorder_at: Decimal
    #: The cell it sits in, or null — not stored anywhere yet, which is a fact.
    cell: str | None = None
    is_active: bool = True

    @property
    def needs_reorder(self) -> bool:
        return self.reorder_at > 0 and self.on_hand <= self.reorder_at


class PlaceArticle(BaseModel):
    address: str = Field(min_length=1, max_length=24)
    note: str | None = Field(default=None, max_length=200)


class TakeArticle(BaseModel):
    """Some of an article leaving the shelf — issued to the floor, or written off."""

    quantity: Decimal = Field(gt=0)
    #: What it went to: a ticket, a parcel, an order. The kit's «Основание».
    reference: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=200)


class ArticleMovementView(BaseModel):
    id: EntityId
    article_id: EntityId
    kind: ArticleKind
    code: str
    name: str
    unit: str
    reason: str
    quantity: Decimal
    on_hand_after: Decimal
    at: datetime
    from_address: str | None = None
    to_address: str | None = None
    reference: str | None = None
    note: str | None = None


__all__ = [
    "ArticleMovementView",
    "ArticleView",
    "PlaceArticle",
    "RegisterArticle",
    "TakeArticle",
]
