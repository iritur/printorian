"""DTOs crossing the ordering boundary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from printorian.contexts.ordering.policies import DeliveryMethod, OrderStatus
from printorian.core.errors import ValidationError
from printorian.core.ids import EntityId


class OrderLineView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    model_name: str
    #: The stored geometry, when the line was placed from an upload.
    model_asset_id: EntityId | None = None
    material_code: str
    quantity: int
    scale: Decimal
    rush: bool
    colors: list[str]
    finishes: list[str]
    estimate_source: str
    estimated_minutes: Decimal
    estimated_grams: Decimal
    line_total: Decimal


class OrderEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    from_status: OrderStatus | None
    to_status: OrderStatus
    reason: str
    created_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class OrderView(BaseModel):
    """An order as the cabinet and the admin table show it."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    number: str
    status: OrderStatus
    customer_id: EntityId | None = None
    customer_email: str
    currency: str
    total: Decimal
    sla_credit: Decimal
    promised_at: datetime | None
    decay_policy: str
    paid_at: datetime | None
    shipped_at: datetime | None
    created_at: datetime

    delivery_method: DeliveryMethod = DeliveryMethod.PICKUP
    delivery_city: str = ""
    delivery_postcode: str = ""
    delivery_address: str = ""
    notify_on_progress: bool = True

    #: The pinned breakdown, exactly as agreed. Never recomputed.
    price_breakdown: dict[str, Any] = Field(default_factory=dict)
    #: ``None`` for an order whose rates are not recoverable — one placed before
    #: snapshots were persisted. The column is nullable precisely so it can say
    #: that, and this must match, or every read of such an order raises.
    rate_snapshot_id: str | None = None
    engine_version: str = ""

    #: Legal next states, from the state machine. The order desk renders its
    #: buttons from this rather than keeping its own copy of the transition table.
    allowed_transitions: list[OrderStatus] = Field(default_factory=list)

    lines: list[OrderLineView] = Field(default_factory=list)
    events: list[OrderEventView] = Field(default_factory=list)

    @property
    def payable_now(self) -> Decimal:
        """What the customer owes after any lateness credit."""
        return max(Decimal(0), self.total - self.sla_credit)


class DraftLine(BaseModel):
    """One configured item on its way into an order."""

    model_name: str = Field(min_length=1, max_length=300)
    #: The asset the configurator quoted, as returned by `/pricing/quote`.
    #: Carrying it is what lets the plate cache be consulted for this order —
    #: without it the prep queue has a filename and no geometry.
    model_asset_id: EntityId | None = None
    material_code: str = Field(min_length=1, max_length=80)
    quantity: int = Field(default=1, ge=1, le=10_000)
    scale: Decimal = Decimal(1)
    rush: bool = False
    colors: list[str] = Field(default_factory=list)
    finishes: list[str] = Field(default_factory=list)

    #: Geometry the quote was based on, carried through from the configurator.
    estimated_minutes: Decimal
    estimated_grams: Decimal
    mesh: dict[str, Any] = Field(default_factory=dict)


class Delivery(BaseModel):
    """Where the order goes, and how.

    Validated as a whole rather than field by field: an address is meaningless for
    collection and required for everything else, and that rule cannot be stated on
    either field alone.
    """

    method: DeliveryMethod = DeliveryMethod.PICKUP
    city: str = Field(default="", max_length=120)
    postcode: str = Field(default="", max_length=20)
    address: str = Field(default="", max_length=400)
    #: An email at every stage change. On by default because the alternative is a
    #: customer refreshing the cabinet to find out whether anything happened.
    notify: bool = True

    @model_validator(mode="after")
    def _address_when_shipped(self) -> Delivery:
        if self.method.is_shipped and not (self.city.strip() and self.address.strip()):
            raise ValidationError("error.ordering.delivery_address_required")
        return self


class RepriceLine(BaseModel):
    """What a configuration costs under a given delivery, before anyone commits.

    Deliberately *not* `PlaceOrder`. That model requires an address for anything
    that ships, which is right when an order is being placed and wrong here: the
    shipping rate is flat, so pricing a courier delivery needs to know only that
    it is one. Reusing `PlaceOrder` meant the checkout could not re-price until an
    address was typed — the moment the customer most wants to see what the choice
    costs.
    """

    method: DeliveryMethod = DeliveryMethod.PICKUP
    lines: list[DraftLine] = Field(min_length=1, max_length=1)


class PlaceOrder(BaseModel):
    customer_email: str = Field(min_length=3, max_length=320)
    #: Exactly one configured model per order for now.
    #:
    #: The configurator produces a single configured model, which is the flow the
    #: scenario describes. A multi-item cart needs a defined rule for combining
    #: separately-priced breakdowns into one, and inventing that quietly here is how
    #: a second pricing path gets born (ADR-0002). It gets its own change when the
    #: cart lands.
    lines: list[DraftLine] = Field(min_length=1, max_length=1)
    #: Where it goes. Priced: collection drops the shipping line entirely.
    delivery: Delivery = Field(default_factory=Delivery)
    #: Set by the farm, not the customer — the configurator only shows it.
    promised_days: int = Field(default=5, ge=1, le=365)
    decay_policy: str = "standard"


class StatusCount(BaseModel):
    status: OrderStatus
    count: int


class OrderTable(BaseModel):
    """Rows plus the counter chips the orders screen shows above them."""

    rows: list[OrderView]
    #: Counts across the *whole* set, not just `rows` — the chips describe the
    #: table, and once there is more than one page a tally of the page would be a
    #: different and wrong number.
    counts: list[StatusCount]
    total: int
    #: Token for the next page, or null on the last one. Opaque: it encodes the
    #: sort key, and a client that parsed it would freeze the ordering in place.
    next_cursor: str | None = None
