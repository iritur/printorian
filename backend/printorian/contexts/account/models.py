"""What a customer keeps between orders: where things go, and when to write.

Neither belongs in `identity`. That context is authentication — who you are and
whether you may be let in — and an address book is not part of proving it. Nor do
they belong in `ordering`: an address outlives the order it was used for, which is
the entire reason for saving one.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from printorian.core.db import Base, Entity
from printorian.core.ids import EntityId


class Address(Entity):
    """One saved delivery address.

    **Copied, never referenced, at checkout.** `Order` carries its own
    `delivery_city` / `delivery_address` columns and this table is not linked to
    them. That is deliberate: an order is a record of what was agreed, and if
    editing an address rewrote where a parcel from March was sent, the farm would
    lose the ability to answer "where did we actually ship it?".
    """

    __tablename__ = "addresses"
    __table_args__ = (
        # Every read of this table is "the addresses of one customer".
        Index("ix_addresses_user_id", "user_id"),
        # No unique index on (user_id, is_default). A partial unique index would
        # express "at most one default" exactly, but it turns the ordinary act of
        # moving the default from A to B into an ordering problem — clear A first
        # or the insert collides mid-statement. The service clears the others in
        # the same transaction, and `tests/unit/test_account.py` pins that it does.
        Index("ix_addresses_user_id_is_default", "user_id", "is_default"),
    )

    #: ``CASCADE``: unlike geometry, an address has no life once its owner is
    #: gone. Orders keep their own copy, so nothing is lost by removing it.
    user_id: Mapped[EntityId] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: What the customer calls it — «Дом», «Офис». Theirs to choose.
    label: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    #: Who receives it, when that is not the account holder.
    recipient: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    postcode: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    address: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    #: Access instructions — «пропуск по паспорту», reception hours. Printed on
    #: the packing slip, which is why it is bounded.
    note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class NotificationPrefs(Base):
    """When to write to one customer. One row per user, created on first read.

    Booleans rather than a JSON blob or a row per event. There are five of them,
    they are named in the design, and each has a different default — a shape that
    a column list states and a key/value table only implies.

    Absent from this table: lateness credit, which cannot be switched off
    (`LATE_CREDIT_IS_MANDATORY`), and the journal, which is a subscription by
    email address rather than by account and lives in `contexts.journal` so that
    people without accounts can have one.
    """

    __tablename__ = "notification_prefs"

    user_id: Mapped[EntityId] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: The receipt. On: somebody who just paid expects to be told it worked.
    on_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The machine and the predicted finish. On: it is the first news worth having.
    on_print_started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Off. Nine stages is nine emails per order, and the kit says so on the row.
    on_every_stage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: On. The parcel has left; this is the one people wait for.
    on_shipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: On. A sign-in nobody recognises is the only warning of a stolen password.
    on_new_sign_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
