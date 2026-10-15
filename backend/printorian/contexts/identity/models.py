"""Persistent models for users, their sessions, and the API keys they issue."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.identity.policies import CustomerKind, Role
from printorian.core.db import Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class User(Entity):
    """A person who can authenticate — customer or staff."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(enum_column(Role), nullable=False, default=Role.CUSTOMER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: BCP-47 tag driving which message catalogue the client renders (ADR-0012).
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")
    #: For the courier, and for nothing else. Free text rather than a parsed
    #: number: this farm ships within one country today, and a validator that
    #: rejects a perfectly good foreign number is worse than no validator.
    phone: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    #: Which set of payment methods and documents applies. See `CustomerKind`.
    customer_kind: Mapped[CustomerKind] = mapped_column(
        enum_column(CustomerKind, length=16), nullable=False, default=CustomerKind.PERSON
    )

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Entity):
    """An authenticated session.

    Only a hash of the token is stored, so a database leak does not hand out live
    sessions. The plaintext token exists once, in the response to sign-in.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id_expires_at", "user_id", "expires_at"),
        # What the reaper sweeps on. Without it the cleanup that keeps this table
        # from growing forever is itself a full scan of the table it is cleaning.
        Index("ix_sessions_expires_at", "expires_at"),
        # No `expires_at > created_at` check, deliberately. `created_at` is a server
        # default — the *database's* clock — while `expires_at` is computed from the
        # injected `Clock`, and the two are different sources on purpose: that
        # injection is what lets the SLA and session tests run against a frozen time.
        # A constraint spanning both would make the application's clock unable to
        # disagree with the server's, which is exactly the freedom the design wants.
    )

    user_id: Mapped[EntityId] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: Where the sign-in came from, as the API saw it.
    #:
    #: Recorded once, at sign-in, and never updated — this answers "was that me?",
    #: and a field that follows the session around answers a different and less
    #: useful question. Behind the trusted proxy it is the address that proxy
    #: saw; `printorian.api.client_address` is the one place that decision is
    #: made.
    client_ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    #: Last time this session was used, to five-minute granularity.
    #:
    #: The granularity is the point. Resolving a token happens on every
    #: authenticated request, and writing a timestamp on each one would put a row
    #: update in front of every read the farm does. `SEEN_GRANULARITY` in the
    #: service is what keeps it to one write per session per five minutes, which
    #: is precise enough for a screen that says "вчера 21:04".
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class ApiKey(Entity):
    """A credential for a machine — a floor screen, an accounting export.

    Stored exactly as a session is: the SHA-256 of the key, never the key, which
    exists once in the response that issued it. `prefix` is its first twelve
    characters, kept so «Ключи API» can say which key is which without holding
    anything that would let a reader use one.

    ``CASCADE`` from the owner: a key is a credential of the person who issued
    it, and a credential must not outlive the account behind it. Deactivating the
    owner stops every key at the next request (`api_keys.resolve`), the same rule
    sessions follow.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        # The `CASCADE` above is checked on every user delete, and PostgreSQL does
        # not index a foreign key for you.
        Index("ix_api_keys_owner_id", "owner_id"),
    )

    owner_id: Mapped[EntityId] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    #: Permission codes, a subset of `policies.KEY_PERMISSIONS` when issued. Stored
    #: as codes rather than an enum array so a permission later removed from the
    #: matrix is skipped at resolution instead of failing every request.
    permissions: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
