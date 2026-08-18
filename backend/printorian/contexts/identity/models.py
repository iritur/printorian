"""Persistent models for users and sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.identity.policies import Role
from printorian.core.db import Entity, UtcDateTime, enum_column
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

    user: Mapped[User] = relationship(back_populates="sessions")
