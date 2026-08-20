"""Shared scaffolding for post-production's API tests.

Helpers only — no fixtures. Importing a fixture by name into another module makes
it shadow the parameter of every test that requests it, which reads to the linter
as a redefinition and to a reader as two things with one name. Each test module
declares its own `client` and `database` from these.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.contexts.identity import CreateUser, IdentityService, Role
from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.postproduction import (
    CreateOperation,
    CreateStep,
    InstructionCatalogue,
    OperationKind,
    PostProductionService,
)
from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.ids import new_id
from tests.factories import ensure_printer

PASSWORD = "correct-horse-battery"

#: `orders.number` and `printers.name` are both unique; one counter serves both.
_labels = count(1)


class PostDatabase:
    """A session factory the tests can reach outside a request."""

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


async def seed_users(database: PostDatabase, settings: Settings, clock: FixedClock) -> None:
    """One account per role, so the permission boundary can be tested."""
    async with database.session_factory() as session:
        identity = IdentityService(session, settings, clock, EventBus())
        for email, role in (
            ("boss@example.com", Role.OWNER),
            ("op@example.com", Role.OPERATOR),
            ("buyer@example.com", Role.CUSTOMER),
        ):
            await identity.create_user(
                CreateUser(email=email, display_name=email, password=PASSWORD, role=role)
            )
        await session.commit()


async def auth(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/auth/sign-in", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def a_sanding_instruction(session: AsyncSession) -> None:
    """Two operations with real instructions: the base one, and a chosen finish."""
    catalogue = InstructionCatalogue(session)
    await catalogue.define_operation(
        CreateOperation(kind=OperationKind.SANDING, norm_minutes_per_unit=Decimal(4)),
        [
            CreateStep(position=1, title="Remove supports", norm_minutes=Decimal(3)),
            CreateStep(position=2, title="Sand to P400", norm_minutes=Decimal(14)),
        ],
    )
    await catalogue.define_operation(
        CreateOperation(kind=OperationKind.SUPPORT_REMOVAL, norm_minutes_per_unit=Decimal(1)),
        [CreateStep(position=1, title="Off the plate", norm_minutes=Decimal(1))],
    )


async def a_paid_order(session: AsyncSession, *, finishes: list[str]) -> Order:
    order = Order(
        number=f"ORD-{next(_labels)}",
        total=Decimal(5000),
        promised_at=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
    )
    session.add(order)
    await session.flush()
    session.add(
        OrderLine(
            order_id=order.id,
            model_name="BRACKET_V4",
            material_code="PETG-CF",
            quantity=10,
            scale=Decimal(1),
            finishes=finishes,
            estimated_minutes=Decimal(180),
            estimated_grams=Decimal(120),
            line_total=Decimal(5000),
        )
    )
    await session.flush()
    return order


async def a_finished_print(session: AsyncSession, order: Order) -> None:
    """A succeeded job, which is what the sweep converts into floor work."""
    printer_id = new_id()
    await ensure_printer(session, printer_id, name=f"P-{next(_labels)}")
    session.add(
        PrintJob(
            order_id=order.id,
            status=JobStatus.SUCCEEDED,
            printer_id=printer_id,
            material_type="PETG-CF",
            grams_required=Decimal(120),
            estimated_minutes=Decimal(180),
            finished_at=datetime(2026, 3, 2, 8, 0, tzinfo=UTC),
        )
    )
    await session.flush()


def a_service(session: AsyncSession, clock: FixedClock) -> PostProductionService:
    return PostProductionService(session, clock, EventBus())


__all__ = [
    "PASSWORD",
    "PostDatabase",
    "a_finished_print",
    "a_paid_order",
    "a_sanding_instruction",
    "a_service",
    "auth",
    "seed_users",
]
