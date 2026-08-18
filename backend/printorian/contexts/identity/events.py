"""Events published by the identity context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from printorian.contexts.identity.policies import Role
from printorian.core.events import Event
from printorian.core.ids import EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class UserRegistered(Event):
    name: ClassVar[str] = "identity.user_registered"

    user_id: EntityId
    role: Role


@dataclass(frozen=True, slots=True, kw_only=True)
class SignInSucceeded(Event):
    name: ClassVar[str] = "identity.sign_in_succeeded"

    user_id: EntityId


@dataclass(frozen=True, slots=True, kw_only=True)
class SignInFailed(Event):
    """Emitted on every rejected attempt — the raw material for lockout and audit."""

    name: ClassVar[str] = "identity.sign_in_failed"

    email: str
