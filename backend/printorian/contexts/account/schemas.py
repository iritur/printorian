"""DTOs crossing the account boundary.

Only what this context owns. The account *screen* also shows orders, uploads and
receipts, and those views belong to the contexts that hold them — the API layer
assembles the page (`api/routers/_account_views.py`).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from printorian.core.errors import ValidationError
from printorian.core.ids import EntityId


class AddressView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    label: str
    recipient: str
    phone: str
    postcode: str
    city: str
    address: str
    note: str
    is_default: bool
    created_at: datetime


class WriteAddress(BaseModel):
    """A new address, or a whole replacement for one.

    `city` and `address` are the two the courier cannot do without, so they are
    the two that are required. Everything else is the customer's convenience —
    a label they will recognise, a different recipient, a note for reception.
    """

    label: str = Field(default="", max_length=80)
    recipient: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    postcode: str = Field(default="", max_length=20)
    city: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1, max_length=400)
    note: str = Field(default="", max_length=300)
    #: Setting this on a save moves the default here, clearing it elsewhere.
    is_default: bool = False

    @model_validator(mode="after")
    def _not_only_whitespace(self) -> WriteAddress:
        if not self.city.strip() or not self.address.strip():
            raise ValidationError("error.account.address_incomplete")
        return self


class NotificationSettings(BaseModel):
    """The switches, plus the two the screen draws but cannot change.

    `on_late_credit` and `journal` are reported alongside the real fields so the
    panel is one object rather than three requests. `on_late_credit` is a constant
    (`LATE_CREDIT_IS_MANDATORY`); `journal` is a subscription by email address,
    owned by `contexts.journal`, and the router composes it in.
    """

    model_config = ConfigDict(from_attributes=True)

    on_paid: bool = True
    on_print_started: bool = True
    on_every_stage: bool = False
    on_shipped: bool = True
    on_new_sign_in: bool = True
    #: Always true. Present so the screen can draw the row on and disabled.
    on_late_credit: bool = True
    #: Whether this address is on the journal's weekly list.
    journal: bool = False


class UpdateNotifications(BaseModel):
    """A partial update. Absent means "leave it alone", read with `exclude_unset`.

    `on_late_credit` is not here: there is no field behind it to set. A client
    that sends it is ignored rather than refused, because the alternative is a
    screen that cannot round-trip the object it was given.
    """

    on_paid: bool | None = None
    on_print_started: bool | None = None
    on_every_stage: bool | None = None
    on_shipped: bool | None = None
    on_new_sign_in: bool | None = None
    #: The journal list. Handled by `contexts.journal`, not by this table.
    journal: bool | None = None


class LadderStep(BaseModel):
    """One rung of the loyalty ladder, as the progress bar draws it."""

    code: str
    from_spend: Decimal
    discount_percent: Decimal
    #: Whether this customer's lifetime spend has reached it.
    reached: bool


class Tier(BaseModel):
    """Where the customer stands, and how far the next rung is.

    The gap is the figure the screen leads with, and it is why this is computed
    here rather than in the browser: a client that subtracts for itself has to
    know the ladder, and then there are two ladders.
    """

    code: str
    discount_percent: Decimal
    lifetime_spend: Decimal
    steps: list[LadderStep] = Field(default_factory=list)
    #: ``None`` at the top of the ladder — there is nothing further to reach.
    next_code: str | None = None
    next_from_spend: Decimal | None = None
    to_next: Decimal | None = None
    #: 0–100, for the bar. Absent at the top, where a bar has nothing to fill.
    progress_percent: Decimal | None = None
