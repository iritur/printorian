"""«Ключи API» — issuing, listing and revoking a machine's read credential.

Owner-only (`MANAGE_USERS`): a key is a credential, and issuing credentials is
the same job as issuing accounts. The routes take `CurrentActor`, which refuses
a key, so a key can neither mint nor revoke keys — `ApiKeyService.issue` checks
it a second time for the day a route forgets.

What a key may *do* is decided elsewhere, and on purpose: `api/deps.requires`
accepts one on reads it holds the permission for, and `CurrentActor` refuses it
everywhere a route acts as a person. The rules are in `identity.api_keys`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from printorian.api.deps import AppClock, CurrentActor, DbSession, requires
from printorian.contexts.identity import (
    KEY_PERMISSIONS,
    ApiKeyService,
    ApiKeyView,
    CreateApiKey,
    IssuedApiKey,
    Permission,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/api-keys",
    tags=["api-keys"],
    dependencies=[Depends(requires(Permission.MANAGE_USERS))],
)


@router.get("")
async def list_keys(db: DbSession, clock: AppClock, _: CurrentActor) -> list[ApiKeyView]:
    """Every key, live first. The prefix and never the key."""
    return await ApiKeyService(db, clock).listing()


@router.get("/permissions")
async def grantable(_: CurrentActor) -> list[str]:
    """What a key may be issued with — the screen's checkboxes, from the one list."""
    return sorted(permission.value for permission in KEY_PERMISSIONS)


@router.post("", status_code=status.HTTP_201_CREATED)
async def issue_key(
    data: CreateApiKey, db: DbSession, clock: AppClock, actor: CurrentActor
) -> IssuedApiKey:
    """Mint a key. The response is the only place the key will ever appear."""
    return await ApiKeyService(db, clock).issue(actor, data)


@router.post("/{key_id}/revoke")
async def revoke_key(
    key_id: EntityId, db: DbSession, clock: AppClock, _: CurrentActor
) -> ApiKeyView:
    """Stop a key at its next request. Kept rather than deleted, so the list still
    says which key it was and when it stopped."""
    return await ApiKeyService(db, clock).revoke(key_id)
