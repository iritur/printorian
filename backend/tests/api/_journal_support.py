"""The world a journal test needs: a database and two people.

An engineer, because `MANAGE_JOURNAL` sits at that tier, and a customer to prove
the gate is real rather than merely declared.

Helpers rather than fixtures — each module declares its own `client`, the way the
catalogue and checkout suites do. Importing a fixture by name shadows the
parameter of every test that takes it, which reads as a redefinition to a linter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.journal.models import JournalPost, JournalSubscriber
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore

PASSWORD = "correct-horse-battery"


class _TestDatabase:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, poolclass=NullPool)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


async def a_journal(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, bus)
        for email, role in (
            ("editor@example.com", Role.ENGINEER),
            ("reader@example.com", Role.CUSTOMER),
        ):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        await session.commit()

    app.state.settings = settings
    app.state.clock = clock
    app.state.event_bus = bus
    app.state.database = database
    app.state.object_store = object_store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http

    await database.dispose()


async def token_for(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def a_report(**overrides: object) -> dict[str, object]:
    return {
        "title": "Час печати",
        "section": "cost",
        "lede": "Сколько на самом деле стоит час работы принтера.",
        "excerpt": "Разбираем полную структуру себестоимости.",
        "author": "Инженерная группа",
        "blocks": [
            {"kind": "heading", "text": "Что входит в час печати"},
            {"kind": "paragraph", "text": "Внутри одного числа восемь независимых статей."},
        ],
        **overrides,
    }


async def write(
    client: AsyncClient, auth: dict[str, str], **overrides: object
) -> dict[str, object]:
    response = await client.post("/journal", json=a_report(**overrides), headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


async def backdate(settings: Settings, title: str, *, weeks: int) -> None:
    """Move a report's publication date into the past.

    Written straight to the column: `create` stamps "now", which is right for a
    report somebody writes and useless for building the history a cadence is
    measured over.
    """
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        post = await session.scalar(select(JournalPost).where(JournalPost.title == title))
        assert post is not None
        assert post.published_at is not None
        post.published_at = post.published_at - timedelta(weeks=weeks)
        await session.commit()
    await database.dispose()


async def subscriber_count(settings: Settings, *, active_only: bool = False) -> int:
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        query = select(func.count()).select_from(JournalSubscriber)
        if active_only:
            query = query.where(JournalSubscriber.unsubscribed_at.is_(None))
        total = await session.scalar(query) or 0
    await database.dispose()
    return int(total)


async def unsubscribe_token(settings: Settings, email: str) -> str:
    """The token, read the way a mail template would read it."""
    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        found = await session.scalar(
            select(JournalSubscriber).where(JournalSubscriber.email == email.lower())
        )
        assert found is not None
        token = found.token
    await database.dispose()
    return token
