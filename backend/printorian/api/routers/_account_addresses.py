"""The address book — «Адреса доставки».

Mounted on `/account` by `account.py`. A module of its own because five routes
over one entity is a coherent thing to read at once, and because the account
router has six sections to carry and a length gate to stay under.

Every route is scoped to the caller by the service, which takes the user id and
the address id together — so an id belonging to somebody else answers *not found*
rather than *forbidden*, and a caller cannot tell the difference between an
address that is not theirs and one that does not exist.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from printorian.api.deps import Account, CurrentActor
from printorian.contexts.account import AddressView, WriteAddress
from printorian.core.ids import EntityId

router = APIRouter(prefix="/addresses")


@router.get("")
async def addresses(actor: CurrentActor, account: Account) -> list[AddressView]:
    """The default first, then the rest oldest-first."""
    return await account.addresses(actor.user_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_address(data: WriteAddress, actor: CurrentActor, account: Account) -> AddressView:
    """Save an address. The first one becomes the default whether asked or not."""
    return await account.add_address(actor.user_id, data)


@router.put("/{address_id}")
async def edit_address(
    address_id: EntityId, data: WriteAddress, actor: CurrentActor, account: Account
) -> AddressView:
    """Replace one wholesale.

    ``PUT`` rather than ``PATCH`` because the form edits every field at once: a
    partial update would make "the customer cleared the flat number" and "the
    customer's form did not include one" the same request.
    """
    return await account.edit_address(actor.user_id, address_id, data)


@router.post("/{address_id}/default")
async def make_default(address_id: EntityId, actor: CurrentActor, account: Account) -> AddressView:
    """Move the default here.

    Its own route rather than a field on the edit form. Promoting an address
    changes *another* row as well as this one, and expressing that as a side
    effect of a save is how two open tabs end up with two defaults.
    """
    return await account.make_default(actor.user_id, address_id)


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address(address_id: EntityId, actor: CurrentActor, account: Account) -> None:
    """Remove one. If it was the default, the oldest survivor inherits."""
    await account.delete_address(actor.user_id, address_id)
