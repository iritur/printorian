"""API keys: a machine's read credential, issued by a person and revocable.

Issue #29's «Ключи API» table (DESIGN-KIT §2.1, «Доступ и безопасность»). The
kit's two keys say what they are for — a floor screen that reads production and
an accounting export that reads every order — and that shapes every rule here:

* **A key reads.** It may hold only `policies.KEY_PERMISSIONS`, and the API's
  `requires` refuses it on any method that is not a read. A leaked key costs a
  disclosure, never a refund or a deleted user.
* **A key is never a person.** `api/deps.CurrentActor` refuses it, so a key
  cannot reach anything that acts *as* its owner — the profile, the password,
  the sessions, the cabinet.
* **A key cannot outgrow its owner.** What it resolves to is the intersection of
  what it was issued with and what the owner's role grants *now*: demote the
  owner and every key they issued shrinks with them; deactivate them and every
  key stops.
* **The key exists once.** Only its SHA-256 is stored, exactly as a session's
  is; the plaintext is in the response that issued it and nowhere else.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity.models import ApiKey, User
from printorian.contexts.identity.policies import KEY_PERMISSIONS, Permission, permissions_for
from printorian.contexts.identity.schemas import Actor, ApiKeyView, CreateApiKey, IssuedApiKey
from printorian.contexts.identity.service import SEEN_GRANULARITY, hash_token
from printorian.core.clock import Clock
from printorian.core.errors import (
    DomainRuleViolationError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from printorian.core.ids import EntityId

#: What every key starts with. The kit prints `pk_live_8f41…`; the prefix is also
#: how `api/deps.get_caller` tells a key from a session token without a query.
KEY_PREFIX = "pk_live_"
#: How much of the key «Ключи API» shows: the marker and four characters of the
#: secret part, enough to tell keys apart and far too little to use one.
SHOWN_CHARACTERS = 12


def is_api_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX)


class ApiKeyService:
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._db = session
        self._clock = clock

    async def issue(self, owner: Actor, data: CreateApiKey) -> IssuedApiKey:
        """Mint a key. The response is the only place the key will ever appear."""
        if owner.api_key_id is not None:
            # `CurrentActor` already refuses a key; this is the second guard, so a
            # future route that forgot it still cannot let a key mint keys.
            raise PermissionDeniedError("error.identity.api_key_not_accepted")
        wanted = frozenset(data.permissions)
        beyond_keys = sorted(p.value for p in wanted - KEY_PERMISSIONS)
        if beyond_keys:
            raise DomainRuleViolationError(
                "error.identity.api_key_permission_not_allowed",
                permissions=beyond_keys,
                allowed=sorted(p.value for p in KEY_PERMISSIONS),
            )
        beyond_owner = sorted(p.value for p in wanted - owner.permissions)
        if beyond_owner:
            raise PermissionDeniedError(
                "error.identity.api_key_exceeds_owner", permissions=beyond_owner
            )

        secret = KEY_PREFIX + secrets.token_urlsafe(32)
        key = ApiKey(
            owner_id=owner.user_id,
            name=data.name.strip(),
            prefix=secret[:SHOWN_CHARACTERS],
            key_hash=hash_token(secret),
            permissions=sorted(p.value for p in wanted),
        )
        self._db.add(key)
        await self._db.flush()
        return IssuedApiKey(key=ApiKeyView.model_validate(key), secret=secret)

    async def listing(self) -> list[ApiKeyView]:
        """Every key, live first, newest first within each."""
        rows = await self._db.scalars(
            select(ApiKey).order_by(ApiKey.revoked_at.is_not(None), ApiKey.created_at.desc())
        )
        return [ApiKeyView.model_validate(row) for row in rows]

    async def revoke(self, key_id: EntityId) -> ApiKeyView:
        """Stop a key at its next request. Kept, not deleted: «кто и когда»."""
        key = await self._db.get(ApiKey, key_id)
        if key is None:
            raise NotFoundError("error.identity.api_key_not_found", key_id=str(key_id))
        if key.revoked_at is None:
            key.revoked_at = self._clock.now()
            await self._db.flush()
        return ApiKeyView.model_validate(key)

    async def resolve(self, token: str) -> Actor:
        """A key to the caller it stands for, or raise as a bad session does."""
        key = await self._db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_token(token)))
        if key is None or key.revoked_at is not None:
            raise UnauthenticatedError("error.identity.invalid_api_key")
        owner = await self._db.get(User, key.owner_id)
        if owner is None or not owner.is_active:
            raise UnauthenticatedError("error.identity.account_disabled")

        known = {permission.value: permission for permission in Permission}
        issued = frozenset(known[code] for code in key.permissions if code in known)
        self._touch(key, self._clock.now())
        return Actor(
            user_id=owner.id,
            email=owner.email,
            display_name=key.name,
            role=owner.role,
            locale=owner.locale,
            permissions=issued & permissions_for(owner.role) & KEY_PERMISSIONS,
            api_key_id=key.id,
        )

    @staticmethod
    def _touch(key: ApiKey, now: datetime) -> None:
        """«Использован», to the granularity sessions use — a floor screen polling
        every few seconds must not turn into a write per request."""
        if key.last_used_at is None or now - key.last_used_at >= SEEN_GRANULARITY:
            key.last_used_at = now


__all__ = ["KEY_PREFIX", "ApiKeyService", "is_api_key"]
