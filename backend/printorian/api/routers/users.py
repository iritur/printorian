"""Staff user administration. Every route is permission-gated."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from printorian.api.deps import CurrentActor, Identity, requires
from printorian.contexts.identity import CreateUser, Permission, Role, UserView
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(requires(Permission.MANAGE_USERS))],
)


@router.get("")
async def list_users(identity: Identity) -> list[UserView]:
    return await identity.list_users()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(data: CreateUser, identity: Identity) -> UserView:
    return await identity.create_user(data)


@router.get("/{user_id}")
async def get_user(user_id: EntityId, identity: Identity) -> UserView:
    return await identity.get_user(user_id)


@router.put("/{user_id}/role")
async def set_role(
    user_id: EntityId, role: Role, identity: Identity, actor: CurrentActor
) -> UserView:
    """Change someone's role. Not your own — see the service for why."""
    return await identity.set_role(user_id, role, actor_id=actor.user_id)


@router.put("/{user_id}/active")
async def set_active(
    user_id: EntityId, is_active: bool, identity: Identity, actor: CurrentActor
) -> UserView:
    """Deactivating a user also revokes every live session they hold."""
    return await identity.set_active(user_id, is_active=is_active, actor_id=actor.user_id)
