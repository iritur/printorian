"""Identity use cases: registration, sign-in, session resolution."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.identity import events, sessions
from printorian.contexts.identity.models import Session, User
from printorian.contexts.identity.policies import STAFF_ROLES, Role, permissions_for
from printorian.contexts.identity.schemas import (
    Actor,
    CreateUser,
    SessionGranted,
    SessionView,
    SignIn,
    UpdateProfile,
    UserView,
)
from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.errors import (
    ConflictError,
    DomainRuleViolationError,
    NotFoundError,
    UnauthenticatedError,
    ValidationError,
)
from printorian.core.events import EventBus
from printorian.core.ids import EntityId

_hasher = PasswordHasher()

#: Verified against when no user matches, so a missing account and a wrong password
#: cost the same time. Without this, sign-in timing enumerates registered emails.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-comparison")

_TOKEN_BYTES = 32

#: Kept in step with ``CreateUser.password``; asserted equal in the identity tests.
MIN_PASSWORD_LENGTH = 10

#: How stale ``Session.last_seen_at`` is allowed to get before a read writes.
#:
#: Every authenticated request resolves a token, so touching the row each time
#: would put an UPDATE in front of every GET the farm serves. Five minutes is
#: finer than the screen that consumes it can show and coarse enough that a busy
#: session costs twelve writes an hour rather than thousands.
SEEN_GRANULARITY = timedelta(minutes=5)


def hash_token(token: str) -> str:
    """SHA-256 of the bearer token. Tokens are high-entropy, so no KDF is needed."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class IdentityService:
    """Use cases for users and sessions."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        clock: Clock,
        bus: EventBus,
    ) -> None:
        self._db = session
        self._settings = settings
        self._clock = clock
        self._bus = bus

    # -- users -----------------------------------------------------------

    async def create_user(self, data: CreateUser) -> UserView:
        email = _normalize_email(data.email)
        existing = await self._db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise ConflictError("error.identity.email_taken", email=email)

        user = User(
            email=email,
            display_name=data.display_name.strip(),
            password_hash=_hasher.hash(data.password),
            role=data.role,
            locale=data.locale,
        )
        self._db.add(user)
        await self._db.flush()

        await self._bus.publish(events.UserRegistered(user_id=user.id, role=user.role))
        return UserView.model_validate(user)

    async def get_user(self, user_id: EntityId) -> UserView:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError("error.identity.user_not_found", user_id=str(user_id))
        return UserView.model_validate(user)

    async def list_users(self) -> list[UserView]:
        # Single-term on purpose (`core.pagination`): unbounded, and a tie swaps
        # two names in a staff list nothing reads by position.
        result = await self._db.scalars(select(User).order_by(User.created_at))
        return [UserView.model_validate(u) for u in result]

    async def display_names(self, ids: Sequence[EntityId] | None = None) -> dict[EntityId, str]:
        """What these people are called.

        Deliberately narrow, and deliberately not `list_users`: a screen that
        wants to put a name beside an operator id should not thereby receive
        everyone's email, role and active flag. Passing no ids answers for every
        staff account, which is what a shift panel needs and is bounded by the
        size of the farm rather than by its customer list.
        """
        query = select(User.id, User.display_name)
        if ids is not None:
            if not ids:
                return {}
            query = query.where(User.id.in_(set(ids)))
        else:
            query = query.where(User.role.in_(STAFF_ROLES))
        rows = await self._db.execute(query)
        return dict(rows.all())  # type: ignore[arg-type]

    async def set_role(
        self, user_id: EntityId, role: Role, *, actor_id: EntityId | None = None
    ) -> UserView:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError("error.identity.user_not_found", user_id=str(user_id))
        if actor_id is not None and user.id == actor_id and role is not user.role:
            # Changing your own role is how a sole owner ends up with a farm that
            # has no owner: `manage_users` becomes unreachable and only a database
            # edit can restore it. Refusing self-changes keeps at least one owner
            # — the one performing the change — entitled at all times.
            raise DomainRuleViolationError("error.identity.cannot_change_own_role")
        user.role = role
        await self._db.flush()
        return UserView.model_validate(user)

    async def set_active(
        self, user_id: EntityId, *, is_active: bool, actor_id: EntityId | None = None
    ) -> UserView:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError("error.identity.user_not_found", user_id=str(user_id))
        if actor_id is not None and user.id == actor_id and not is_active:
            # Deactivation revokes every live session, so this would sign the
            # caller out mid-request and lock them out of the screen they did it from.
            raise DomainRuleViolationError("error.identity.cannot_deactivate_self")
        user.is_active = is_active
        if not is_active:
            await self._revoke_all(user.id)
        await self._db.flush()
        return UserView.model_validate(user)

    async def update_profile(self, user_id: EntityId, data: UpdateProfile) -> UserView:
        """Change what someone may change about themselves.

        `exclude_unset`, so a screen that edits one field sends one field and the
        rest are left as they were rather than blanked. `display_name` is stripped
        and must survive it — a name of three spaces is an empty name with extra
        steps, and the column is not nullable.
        """
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError("error.identity.user_not_found", user_id=str(user_id))

        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        if "display_name" in changes:
            name = str(changes["display_name"]).strip()
            if not name:
                raise ValidationError("error.identity.display_name_required")
            changes["display_name"] = name
        if "phone" in changes:
            changes["phone"] = str(changes["phone"]).strip()

        for field, value in changes.items():
            setattr(user, field, value)
        await self._db.flush()
        return UserView.model_validate(user)

    # -- authentication --------------------------------------------------

    async def sign_in(
        self, data: SignIn, *, user_agent: str | None = None, client_ip: str = ""
    ) -> SessionGranted:
        email = _normalize_email(data.email)
        user = await self._db.scalar(select(User).where(User.email == email))

        if user is None:
            # Spend the same time as a real verification before failing.
            self._verify_or_none(_DUMMY_HASH, data.password)
            await self._bus.publish(events.SignInFailed(email=email))
            raise UnauthenticatedError("error.identity.invalid_credentials")

        if not self._verify_or_none(user.password_hash, data.password):
            await self._bus.publish(events.SignInFailed(email=email))
            raise UnauthenticatedError("error.identity.invalid_credentials")

        if not user.is_active:
            raise UnauthenticatedError("error.identity.account_disabled")

        granted = await self._issue_session(user, user_agent=user_agent, client_ip=client_ip)
        await self._bus.publish(events.SignInSucceeded(user_id=user.id))
        return granted

    async def resolve(self, token: str) -> Actor:
        """Resolve a bearer token to an :class:`Actor`, or raise."""
        if not token:
            raise UnauthenticatedError("error.identity.missing_token")

        session = await self._db.scalar(
            select(Session).where(Session.token_hash == hash_token(token))
        )
        if session is None or session.revoked_at is not None:
            raise UnauthenticatedError("error.identity.invalid_session")
        if session.expires_at <= self._clock.now():
            raise UnauthenticatedError("error.identity.session_expired")

        user = await self._db.get(User, session.user_id)
        if user is None or not user.is_active:
            raise UnauthenticatedError("error.identity.account_disabled")

        self._touch(session)
        return actor_of(user)

    async def sign_out(self, token: str) -> None:
        session = await self._db.scalar(
            select(Session).where(Session.token_hash == hash_token(token))
        )
        if session is not None and session.revoked_at is None:
            session.revoked_at = self._clock.now()
            await self._db.flush()

    # -- sessions --------------------------------------------------------

    async def list_sessions(self, user_id: EntityId, *, current: str = "") -> list[SessionView]:
        """Live sessions, newest first, with the caller's own one marked."""
        return await sessions.live_sessions(
            self._db,
            user_id,
            now=self._clock.now(),
            current_hash=hash_token(current) if current else "",
        )

    async def revoke_session(self, user_id: EntityId, session_id: EntityId) -> None:
        """End one session. Scoped by owner, so an id from elsewhere finds nothing."""
        await sessions.revoke_one(self._db, user_id, session_id, now=self._clock.now())

    async def revoke_others(self, user_id: EntityId, *, keep: str) -> int:
        """End every session except the one making the request. Returns the count."""
        return await sessions.revoke_others(
            self._db,
            user_id,
            keep_hash=hash_token(keep) if keep else "",
            now=self._clock.now(),
        )

    def _touch(self, session: Session) -> None:
        """Record that this session was used, at `SEEN_GRANULARITY`."""
        now = self._clock.now()
        if session.last_seen_at is None or now - session.last_seen_at >= SEEN_GRANULARITY:
            session.last_seen_at = now

    # -- passwords -------------------------------------------------------

    async def change_password(self, user_id: EntityId, *, current: str, replacement: str) -> None:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError("error.identity.user_not_found", user_id=str(user_id))
        if not self._verify_or_none(user.password_hash, current):
            raise UnauthenticatedError("error.identity.invalid_credentials")
        if len(replacement) < MIN_PASSWORD_LENGTH:
            raise ValidationError("error.identity.password_too_short", minimum=MIN_PASSWORD_LENGTH)

        user.password_hash = _hasher.hash(replacement)
        await self._revoke_all(user.id)
        await self._db.flush()

    # -- housekeeping ----------------------------------------------------

    async def purge_expired_sessions(self, *, grace: timedelta = timedelta(days=7)) -> int:
        """Delete sessions that expired longer than ``grace`` ago. Returns the count.

        Nothing has ever removed a row from this table. With a twelve-hour TTL and
        no reaper, `sessions` grows monotonically for the life of the deployment —
        every sign-in, forever — and it is on the authentication path, so it is the
        one table where unchecked growth is felt on every request.

        The grace period keeps recently-expired rows readable for a little while, so
        "your session ended" can still be distinguished from "that token was never
        real" while anyone is still looking. Beyond it, an expired session answers no
        question worth the storage.

        A bulk ``DELETE`` rather than loading rows and deleting them one at a time:
        this is a sweep over potentially very many rows and none of their contents
        matter. The `expires_at` index is what keeps it from scanning the table it
        is cleaning.
        """
        cutoff = self._clock.now() - grace
        result = await self._db.execute(delete(Session).where(Session.expires_at < cutoff))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    # -- internals -------------------------------------------------------

    async def _issue_session(
        self, user: User, *, user_agent: str | None, client_ip: str = ""
    ) -> SessionGranted:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = self._clock.now() + timedelta(hours=self._settings.session_ttl_hours)

        self._db.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=expires_at,
                user_agent=(user_agent or "")[:300] or None,
                client_ip=client_ip[:45],
                last_seen_at=self._clock.now(),
            )
        )
        await self._db.flush()
        return SessionGranted(token=token, expires_at=expires_at, actor=actor_of(user))

    async def _revoke_all(self, user_id: EntityId) -> None:
        sessions = await self._db.scalars(
            select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        )
        now = self._clock.now()
        for session in sessions:
            session.revoked_at = now

    @staticmethod
    def _verify_or_none(password_hash: str, password: str) -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError):
            return False


def actor_of(user: User) -> Actor:
    """Project a persisted user into the immutable caller identity."""
    return Actor(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        locale=user.locale,
        permissions=permissions_for(user.role),
    )
