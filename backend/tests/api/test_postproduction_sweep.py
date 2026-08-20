"""The sweep that fills the post-production board.

The board's own footer promises that a task appears when a printer finishes, and
this is what keeps it. The sweep is **reconciling rather than reactive** — it asks
which succeeded prints have no task yet — so what is worth pinning is that it
converts, that it does not convert twice, and that a print with no chosen finish
still reaches the floor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.workers.postproduction import PostProductionSweep, SweepOutcome
from tests.api._postproduction_support import (
    PostDatabase,
    a_finished_print,
    a_paid_order,
    a_sanding_instruction,
    a_service,
)


@pytest.fixture
async def database(settings: Settings, clean_database: None) -> AsyncIterator[PostDatabase]:
    database = PostDatabase(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def one_pass(session: AsyncSession, clock: FixedClock) -> SweepOutcome:
    return await PostProductionSweep(session, a_service(session, clock), clock).sweep()


async def a_farm_with_one_finished_print(database: PostDatabase, *, finishes: list[str]) -> None:
    async with database.session_factory() as session:
        await a_sanding_instruction(session)
        order = await a_paid_order(session, finishes=finishes)
        await a_finished_print(session, order)
        await session.commit()


async def test_a_finished_print_becomes_floor_work_without_anybody_asking(
    database: PostDatabase, clock: FixedClock
) -> None:
    """A queue somebody has to remember to add to is wrong by the first busy shift."""
    await a_farm_with_one_finished_print(database, finishes=["sanded"])

    async with database.session_factory() as session:
        outcome = await one_pass(session, clock)
        await session.commit()

    # Support removal always, plus the sanding the customer paid for.
    assert outcome.raised == 2


async def test_the_sweep_does_not_raise_the_same_batch_twice(
    database: PostDatabase, clock: FixedClock
) -> None:
    """Reconciling rather than reactive: a restart mid-pass must be harmless."""
    await a_farm_with_one_finished_print(database, finishes=["sanded"])

    async with database.session_factory() as session:
        await one_pass(session, clock)
        await session.commit()
    async with database.session_factory() as session:
        second = await one_pass(session, clock)
        await session.commit()

    assert second.raised == 0


async def test_a_print_with_no_finish_still_reaches_the_floor(
    database: PostDatabase, clock: FixedClock
) -> None:
    """Choosing no finish still means somebody takes the part off the plate."""
    await a_farm_with_one_finished_print(database, finishes=[])

    async with database.session_factory() as session:
        outcome = await one_pass(session, clock)
        await session.commit()

    assert outcome.raised == 1
