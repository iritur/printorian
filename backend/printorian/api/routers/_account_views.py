"""The shapes the account screen reads, composed across contexts.

These live in the API layer rather than in `contexts.account` on purpose. The
account screen is a *view* over five contexts — identity, ordering, catalog,
payments and pricing — and a context that reached into all five to assemble one
page would be the shared-DbContext mistake the boundary exists to prevent. The
delivery layer sits above every context and is the one place composition is
allowed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from printorian.contexts.account import Tier
from printorian.contexts.catalog import ModelAssetView
from printorian.contexts.identity import UserView
from printorian.contexts.ordering import Lifetime
from printorian.core.ids import EntityId


class Overview(BaseModel):
    """Everything above the tab rail: who, which tier, and the lifetime figures."""

    profile: UserView
    tier: Tier
    lifetime: Lifetime


class ShelvedModel(BaseModel):
    """One uploaded mesh with the one fact the catalogue cannot supply.

    `orders` is counted in `ordering`, over this customer's own orders only —
    uploads are deduplicated by content address, so counting globally would tell
    one customer how often another had printed the same part.
    """

    asset: ModelAssetView
    orders: int = 0


class Shelf(BaseModel):
    """«Мои модели», including the figures in its footer."""

    models: list[ShelvedModel] = Field(default_factory=list)
    used_bytes: int = 0
    quota_bytes: int = 0


class Receipt(BaseModel):
    """A document row: a settled payment or a succeeded refund, named by order.

    Derived, never stored — see `payments.PaymentDocument`. What this adds is the
    order *number*, because the id a payment carries is not something a customer
    can match against anything they have been shown.
    """

    kind: str
    order_id: EntityId
    order_number: str
    provider: str
    amount: Decimal
    currency: str
    issued_at: datetime
