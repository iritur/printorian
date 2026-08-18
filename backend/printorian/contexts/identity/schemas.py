"""DTOs crossing the identity boundary."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from printorian.contexts.identity.policies import Permission, Role
from printorian.core.ids import EntityId


class Actor(BaseModel):
    """The authenticated caller, as every other layer sees them.

    Carries resolved permissions so downstream code never re-derives them from the
    role — one matrix, one answer (:mod:`printorian.contexts.identity.policies`).
    """

    model_config = ConfigDict(frozen=True)

    user_id: EntityId
    email: str
    display_name: str
    role: Role
    locale: str
    permissions: frozenset[Permission]

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    email: str
    display_name: str
    role: Role
    is_active: bool
    locale: str
    created_at: datetime


class CreateUser(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=200)
    role: Role = Role.CUSTOMER
    locale: str = Field(default="ru", pattern="^(ru|en)$")


class SignIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class SessionGranted(BaseModel):
    """Returned once, at sign-in. The token is never retrievable again."""

    token: str
    expires_at: datetime
    actor: Actor
