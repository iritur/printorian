"""The customer's own record: their addresses and when to write to them.

Deliberately narrow. The account *screen* also shows orders, uploaded models,
receipts and a loyalty ladder, and none of those are here — they belong to
`ordering`, `catalog`, `payments` and `pricing` respectively, and a service that
reached into four other contexts' tables to assemble one page would be exactly the
shared-DbContext mistake the boundary exists to prevent. The API layer sits above
all of them and is where that page is composed.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.account.models import Address, NotificationPrefs
from printorian.contexts.account.policies import LATE_CREDIT_IS_MANDATORY, MAX_ADDRESSES
from printorian.contexts.account.schemas import (
    AddressView,
    NotificationSettings,
    UpdateNotifications,
    WriteAddress,
)
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId


class AccountService:
    """Addresses and notification preferences for one customer at a time."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # -- addresses -------------------------------------------------------

    async def addresses(self, user_id: EntityId) -> list[AddressView]:
        """The default first, then the rest oldest-first.

        The order is the screen's, and it is stable: a list that reshuffles when
        somebody edits a label is a list people stop trusting to point at.
        """
        rows = await self._db.scalars(
            select(Address)
            .where(Address.user_id == user_id)
            # `id` last so this list and `delete_address` agree about which
            # address is «the oldest» — a screen that disagreed with the rule
            # picking the heir would be worse than either order alone.
            .order_by(Address.is_default.desc(), Address.created_at, Address.id)
        )
        return [AddressView.model_validate(row) for row in rows]

    async def add_address(self, user_id: EntityId, data: WriteAddress) -> AddressView:
        """Save a new address. The first one is the default whether asked or not.

        Somebody with exactly one address has no meaningful choice to make, and
        leaving it unmarked would mean the checkout's picker opens with nothing
        selected on the most common account there is.
        """
        held = await self._count(user_id)
        if held >= MAX_ADDRESSES:
            raise DomainRuleViolationError("error.account.too_many_addresses", limit=MAX_ADDRESSES)

        wanted = data.is_default or held == 0
        if wanted:
            await self._clear_default(user_id)

        row = Address(user_id=user_id, **self._fields(data), is_default=wanted)
        self._db.add(row)
        await self._db.flush()
        return AddressView.model_validate(row)

    async def edit_address(
        self, user_id: EntityId, address_id: EntityId, data: WriteAddress
    ) -> AddressView:
        """Replace one address wholesale.

        `is_default=False` on an address that *is* the default is ignored rather
        than obeyed. Obeying it would leave the customer with addresses and no
        default — a state nothing else in the system knows how to render — and
        the way to move a default is to set it somewhere else.
        """
        row = await self._owned(user_id, address_id)
        for field, value in self._fields(data).items():
            setattr(row, field, value)
        if data.is_default and not row.is_default:
            await self._clear_default(user_id)
            row.is_default = True
        await self._db.flush()
        return AddressView.model_validate(row)

    async def make_default(self, user_id: EntityId, address_id: EntityId) -> AddressView:
        row = await self._owned(user_id, address_id)
        await self._clear_default(user_id)
        row.is_default = True
        await self._db.flush()
        return AddressView.model_validate(row)

    async def delete_address(self, user_id: EntityId, address_id: EntityId) -> None:
        """Remove one. If it was the default, the oldest survivor inherits.

        Not "no default until you pick one": the customer deleting an old address
        is not asking to be sent to the checkout to choose again.
        """
        row = await self._owned(user_id, address_id)
        was_default = row.is_default
        await self._db.delete(row)
        await self._db.flush()

        if not was_default:
            return
        heir = await self._db.scalar(
            # `id` as well as time: addresses added in one checkout share the
            # transaction's `now()`, so «the oldest» has to mean something when
            # they tie (`core.pagination`).
            select(Address)
            .where(Address.user_id == user_id)
            .order_by(Address.created_at, Address.id)
            .limit(1)
        )
        if heir is not None:
            heir.is_default = True
            await self._db.flush()

    # -- notifications ---------------------------------------------------

    async def notifications(
        self, user_id: EntityId, *, journal: bool = False
    ) -> NotificationSettings:
        """The switches for one customer, defaults included.

        No row means nobody has touched the panel, which is not the same as
        everything off — the defaults are on the columns, so an absent row reads
        as the shipped behaviour. Nothing is written by a read.
        """
        row = await self._db.get(NotificationPrefs, user_id)
        base = (
            NotificationSettings.model_validate(row) if row is not None else NotificationSettings()
        )
        return base.model_copy(
            update={"on_late_credit": LATE_CREDIT_IS_MANDATORY, "journal": journal}
        )

    async def set_notifications(
        self, user_id: EntityId, data: UpdateNotifications, *, journal: bool = False
    ) -> NotificationSettings:
        """Apply a partial update, creating the row on first write."""
        row = await self._db.get(NotificationPrefs, user_id)
        if row is None:
            row = NotificationPrefs(user_id=user_id)
            self._db.add(row)

        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        # The journal is a subscription by email address, not a column here. The
        # router applies it against `contexts.journal` and reports the outcome.
        changes.pop("journal", None)
        for field, value in changes.items():
            setattr(row, field, value)
        await self._db.flush()

        return NotificationSettings.model_validate(row).model_copy(
            update={"on_late_credit": LATE_CREDIT_IS_MANDATORY, "journal": journal}
        )

    # -- internals -------------------------------------------------------

    @staticmethod
    def _fields(data: WriteAddress) -> dict[str, str]:
        """The address itself, trimmed. Defaulting is decided separately."""
        return {
            "label": data.label.strip(),
            "recipient": data.recipient.strip(),
            "phone": data.phone.strip(),
            "postcode": data.postcode.strip(),
            "city": data.city.strip(),
            "address": data.address.strip(),
            "note": data.note.strip(),
        }

    async def _owned(self, user_id: EntityId, address_id: EntityId) -> Address:
        """Fetch one address, scoped by owner.

        Not found rather than forbidden for somebody else's id, for the same
        reason sign-in takes constant time: a caller who can tell the two apart
        can enumerate what exists.
        """
        row = await self._db.scalar(
            select(Address).where(Address.id == address_id, Address.user_id == user_id)
        )
        if row is None:
            raise NotFoundError("error.account.address_not_found", address_id=str(address_id))
        return row

    async def _count(self, user_id: EntityId) -> int:
        held = await self._db.scalar(
            select(func.count()).select_from(Address).where(Address.user_id == user_id)
        )
        return int(held or 0)

    async def _clear_default(self, user_id: EntityId) -> None:
        """Unset every default this customer holds, before setting one.

        Loaded and mutated rather than a bulk UPDATE: the rows are few, and an
        `UPDATE` behind the session's back leaves any already-loaded `Address`
        stale in the identity map — which is how the caller ends up returning the
        old default to the screen it just changed.
        """
        rows = await self._db.scalars(
            select(Address).where(Address.user_id == user_id, Address.is_default.is_(True))
        )
        for row in rows:
            row.is_default = False
