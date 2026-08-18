"""Row factories for tests.

PostgreSQL enforces foreign keys and SQLite did not (ADR-0021), so a great many
tests that used to build a job against a fabricated `order_id` now need the parent
row to exist. These make one, minimally: the tests that call them are about
scheduling, dispatch and the prep queue, not about ordering — the row exists so
the key resolves.

Separate from `conftest.py` because that file is at the 400-line gate, and because
a factory is a different thing from a fixture: these take a session and are called
from a test body, not requested by name.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_order(session: AsyncSession, order_id: object, *, number: str = "TEST-1") -> None:
    """Make sure a job's order actually exists.

    PostgreSQL enforces foreign keys; SQLite did not, so a great many tests built a
    `PrintJob` against an `order_id` that referenced nothing — a state production
    cannot reach, since a job only ever comes into being from an order. On the real
    database those tests were asserting about an impossible world.

    Minimal on purpose: these tests are about scheduling, dispatch and the prep
    queue, not about ordering. The row exists so the key resolves.
    """
    from printorian.contexts.ordering.models import Order

    if await session.get(Order, order_id) is not None:
        return
    session.add(Order(id=order_id, number=number))
    await session.flush()


async def ensure_plate(session: AsyncSession, plate_id: object, *, key: str = "test-plate") -> None:
    """Make sure a plate a job is being pointed at actually exists.

    Same reason as `ensure_order`: `print_jobs.prepared_plate_id` is a real
    foreign key, and the tests that attach a plate are about the *variance band*,
    not about where the plate came from.
    """
    from printorian.contexts.catalog.models import PreparedPlate

    if await session.get(PreparedPlate, plate_id) is not None:
        return
    session.add(PreparedPlate(id=plate_id, plate_key=key))
    await session.flush()


async def ensure_user(
    session: AsyncSession, user_id: object, *, email: str = "someone@example.test"
) -> None:
    """Make sure a user a row points at actually exists.

    `orders.customer_id` and `prepared_plates.sliced_by` are real foreign keys.
    """
    from printorian.contexts.identity.models import User

    if await session.get(User, user_id) is not None:
        return
    session.add(User(id=user_id, email=email, display_name=email, password_hash="x"))
    await session.flush()


async def ensure_lot(session: AsyncSession, lot_id: object, *, code: str = "PLA-TEST") -> None:
    """Make sure a lot an AMS slot is being pointed at actually exists.

    `ams_slots.lot_id` is a real foreign key, and a lot needs the spec it is a lot
    *of* — so both rows are created here. The whole point of the constraint is that
    the material ? slot ? printer triangle is checkable, which is what the
    scheduler's hard eligibility filter runs on.
    """
    from decimal import Decimal as _Decimal

    from printorian.contexts.inventory.models import MaterialLot, MaterialSpec

    if await session.get(MaterialLot, lot_id) is not None:
        return
    spec = await session.scalar(
        __import__("sqlalchemy").select(MaterialSpec).where(MaterialSpec.code == code)
    )
    if spec is None:
        spec = MaterialSpec(
            code=code, name=code, family="PLA", sell_price_per_gram=_Decimal("2.50")
        )
        session.add(spec)
        await session.flush()
    session.add(
        MaterialLot(
            id=lot_id,
            spec_id=spec.id,
            initial_grams=_Decimal(1000),
            remaining_grams=_Decimal(1000),
        )
    )
    await session.flush()


async def ensure_printer(session: AsyncSession, printer_id: object, *, name: str = "P") -> None:
    """Make sure a machine a job is being assigned to actually exists.

    `print_jobs.printer_id` is a real foreign key. The planner is handed
    `SchedulablePrinter` DTOs, which are deliberately not database rows — so a test
    that invents one has to put the machine in the fleet as well, or it is planning
    onto hardware the farm does not own.
    """
    from printorian.contexts.fleet.models import Printer

    if await session.get(Printer, printer_id) is not None:
        return
    session.add(Printer(id=printer_id, name=name))
    await session.flush()
