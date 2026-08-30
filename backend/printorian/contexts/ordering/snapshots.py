"""Reading back the rates an order was priced at.

ADR-0020's guarantee has been real and tested since the snapshot table landed:
raising a margin changes the next quote and nothing already sold. What was
missing is the ability for anyone to *look* at it. The settings screen lets an
owner change seventeen pricing rates, audited «было · стало»; a customer asks why
a repeat order costs more than last month's; the system holds both snapshots and
could show neither. The audit log answers *what changed and when*. This answers
what *this order* was priced against, which is the question actually being asked.

**The payload is served as it was stored, and not rebuilt.**
`pricing.rates_from_dict` exists and is the wrong tool here: it skips fields
absent from a stored row and `RateSnapshot` then supplies today's defaults for
them, so a snapshot written before a rate existed would come back carrying a
number that was never in force. That is ADR-0007 exactly — an invented figure,
indistinguishable from a measured one. A row from an older schema version shows
the fields it has and no others, and `schema_version` inside the payload says
which vintage it is.

Kept out of `OrderingService` because `service.py` is at the 400-line gate, and
because the read wants none of what the service carries — no clock, no bus, no
transaction. It is one row by primary key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering.models import Order, RateSnapshotRecord
from printorian.core.errors import NotFoundError
from printorian.core.ids import EntityId


class RateSnapshotView(BaseModel):
    """Every rate one order's price was built from, exactly as it was pinned."""

    model_config = ConfigDict(from_attributes=True)

    #: The content hash, which is also the primary key: identical rates *are* the
    #: same snapshot. Comparing this between two orders is how "why does this cost
    #: more than last month's" gets an answer — equal ids mean the rates did not
    #: move and the difference is in the configuration.
    id: str
    #: The engine that read these rates. A snapshot alone does not fix a result;
    #: the calculation shape has to be pinned with it (ADR-0002).
    engine_version: str
    #: The stored rates verbatim, as `pricing.rates_to_dict` wrote them — keys and
    #: numbers, never labels. The client owns the words (ADR-0012), and it already
    #: has them: the settings screen carries a `settings.field.pricing.*` entry
    #: for every rate in here.
    payload: dict[str, Any]
    created_at: datetime


async def rate_snapshot_for(db: AsyncSession, order_id: EntityId) -> RateSnapshotView:
    """The rates ``order_id`` was quoted at.

    Raises rather than returning an empty shape in both of the ways this can have
    no answer, and the two are deliberately different codes:

    * the order does not exist — `error.ordering.not_found`;
    * the order predates ADR-0020 and pinned no snapshot —
      `error.ordering.rates_not_recorded`. `Order.rate_snapshot_id` is nullable
      precisely so it can say that, and a screen must render "not recorded"
      rather than a table of zeros.
    """
    order = await db.get(Order, order_id)
    if order is None:
        raise NotFoundError("error.ordering.not_found", order_id=str(order_id))
    if order.rate_snapshot_id is None:
        raise NotFoundError("error.ordering.rates_not_recorded", order_id=str(order_id))

    record = await db.get(RateSnapshotRecord, order.rate_snapshot_id)
    if record is None:
        # The foreign key is `RESTRICT`, so this is unreachable while the database
        # is intact — and it is checked anyway, because the alternative to raising
        # here is returning `None.payload` from a money endpoint.
        raise NotFoundError(
            "error.ordering.rates_not_recorded",
            order_id=str(order_id),
            snapshot_id=order.rate_snapshot_id,
        )
    return RateSnapshotView.model_validate(record)


__all__ = ["RateSnapshotView", "rate_snapshot_for"]
