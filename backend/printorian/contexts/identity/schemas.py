"""DTOs crossing the identity boundary."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from printorian.contexts.identity.policies import CustomerKind, Permission, Role
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
    #: Set when the caller is a machine holding an API key rather than a person
    #: with a session. `user_id` is then the key's owner — for attribution, not
    #: for acting as them — and `CurrentActor` refuses the caller outright.
    api_key_id: EntityId | None = None

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
    phone: str = ""
    customer_kind: CustomerKind = CustomerKind.PERSON
    created_at: datetime


class UpdateProfile(BaseModel):
    """What someone may change about themselves.

    Not `role`, not `is_active`, not `email`. The first two are somebody else's
    decision, and the third is the login — changing it needs the new address
    proved, which needs mail the farm cannot yet send. It is absent rather than
    accepted-and-ignored.

    Every field is optional and read with `exclude_unset`, so a screen that edits
    one field sends one field.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    locale: str | None = Field(default=None, pattern="^(ru|en)$")
    customer_kind: CustomerKind | None = None


class ChangePassword(BaseModel):
    """The current one proves it is you; the length rule is `MIN_PASSWORD_LENGTH`."""

    current: str = Field(min_length=1, max_length=200)
    replacement: str = Field(min_length=1, max_length=200)


class SessionView(BaseModel):
    """One live sign-in, as the security screen lists them.

    No token and no hash of one. This is a list a browser renders; the only thing
    it needs is enough to recognise a device and an id to end it by.
    """

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    user_agent: str | None = None
    client_ip: str = ""
    created_at: datetime
    last_seen_at: datetime | None = None
    expires_at: datetime
    #: Whether this is the session making the request. The screen refuses to end
    #: it from the row, because doing so signs you out of the screen you did it on.
    is_current: bool = False


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


# -- API keys ----------------------------------------------------------------


class CreateApiKey(BaseModel):
    """«Создать ключ»: a name somebody will recognise, and what the key may read."""

    name: str = Field(min_length=1, max_length=80)
    permissions: list[Permission] = Field(min_length=1)


class ApiKeyView(BaseModel):
    """One row of «Ключи API». Never the key, never its hash — the prefix only."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    name: str
    prefix: str
    permissions: list[str]
    created_at: datetime
    #: Null when the key has never been used. Every use is recorded (to five
    #: minutes, as sessions are), so null is a measurement: "never", not "unknown".
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class IssuedApiKey(BaseModel):
    """The one response that carries the key itself. It is not stored, and cannot
    be shown again — the screen says so, as the kit does."""

    key: ApiKeyView
    secret: str
