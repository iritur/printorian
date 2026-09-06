"""«Заказан» — the status that used to be a flag somebody had to remember to clear.

`material_specs.has_open_order` was a stored boolean whose own comment called it a
placeholder until purchase orders existed. They exist now, so the answer is
computed from `purchase_order_lines`. These are the cases that make the
replacement worth having: a flag cannot fall back to false when an order is
cancelled, and a flag nobody set cannot become true when one is raised.

The other half of the change is the *shape* of the read. `InventoryService.table`
and `get_by_code` take `on_order` as a required keyword with no empty default,
because a caller that forgot to ask procurement would otherwise be told "nothing
is on order" — a claim about the farm rather than an admission that nobody looked.
`test_the_required_keyword_is_what_makes_a_forgotten_caller_visible` pins that.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    CreateMaterialLot,
    CreateMaterialSpec,
    InventoryService,
    MaterialStatus,
)
from printorian.contexts.procurement import (
    CreatePurchaseLine,
    CreatePurchaseOrder,
    ProcurementService,
    PurchasableKind,
    PurchaseStatus,
    ordered_codes,
)
from printorian.core.clock import FixedClock

MATERIAL = "PLA-BLACK"


@pytest.fixture
def procurement(db_session: AsyncSession, clock: FixedClock) -> ProcurementService:
    return ProcurementService(db_session, clock, lots=InventoryService(db_session))


@pytest.fixture
def inventory(db_session: AsyncSession) -> InventoryService:
    return InventoryService(db_session)


@pytest.fixture(autouse=True)
async def _a_filament_with_nothing_left(inventory: InventoryService) -> None:
    """Empty on purpose: `ORDERED` only shows when there is no stock and nothing
    mounted, which is the whole question the status answers."""
    await inventory.create_spec(CreateMaterialSpec(code=MATERIAL, name="PLA Black", family="PLA"))


async def an_order_at(procurement: ProcurementService, status: PurchaseStatus) -> None:
    """One order for MATERIAL, walked to ``status`` along a legal path."""
    order = await procurement.raise_order(
        CreatePurchaseOrder(
            lines=[
                CreatePurchaseLine(
                    kind=PurchasableKind.MATERIAL,
                    item_code=MATERIAL,
                    quantity=Decimal(1000),
                    unit="gram",
                )
            ]
        )
    )
    path: dict[PurchaseStatus, tuple[PurchaseStatus, ...]] = {
        PurchaseStatus.DRAFT: (),
        PurchaseStatus.APPROVED: (PurchaseStatus.APPROVED,),
        PurchaseStatus.CANCELLED: (PurchaseStatus.APPROVED, PurchaseStatus.CANCELLED),
    }
    for stage in path[status]:
        await procurement.advance(order.id, stage)


async def status_of(inventory: InventoryService, db_session: AsyncSession) -> MaterialStatus:
    """The spec's status, read back from the database rather than from the
    session's memory — `expire_all` because a lot added a moment ago is otherwise
    answered out of an identity map that already had the collection."""
    db_session.expire_all()
    view = await inventory.get_by_code(MATERIAL, on_order=await ordered_codes(db_session))
    return view.status


async def test_a_spec_with_an_open_order_line_reads_ordered(
    procurement: ProcurementService, inventory: InventoryService, db_session: AsyncSession
) -> None:
    await an_order_at(procurement, PurchaseStatus.APPROVED)

    assert await status_of(inventory, db_session) is MaterialStatus.ORDERED


async def test_a_draft_nobody_approved_does_not_claim_the_filament_is_coming(
    procurement: ProcurementService, inventory: InventoryService, db_session: AsyncSession
) -> None:
    """A draft is a thought. Letting one suppress the shortage would mean an order
    nobody ever approved quietly stopped the farm being told it is out."""
    await an_order_at(procurement, PurchaseStatus.DRAFT)

    assert await ordered_codes(db_session) == frozenset()
    assert await status_of(inventory, db_session) is MaterialStatus.NONE


async def test_a_cancelled_order_stops_making_a_spec_read_ordered(
    procurement: ProcurementService, inventory: InventoryService, db_session: AsyncSession
) -> None:
    """The failure the stored flag actually produced: a supplier fell through and
    the material kept reading «Заказан» because nobody went back to clear it."""
    await an_order_at(procurement, PurchaseStatus.CANCELLED)

    assert await status_of(inventory, db_session) is MaterialStatus.NONE


async def test_stock_on_the_shelf_still_wins_over_an_open_order(
    procurement: ProcurementService, inventory: InventoryService, db_session: AsyncSession
) -> None:
    """«Где я могу это напечатать прямо сейчас» is the question the column
    answers, so a spool in hand outranks one on its way."""
    await an_order_at(procurement, PurchaseStatus.APPROVED)
    await inventory.add_lot(CreateMaterialLot(spec_code=MATERIAL, initial_grams=Decimal(800)))

    assert await status_of(inventory, db_session) is MaterialStatus.STOCK


async def test_the_required_keyword_is_what_makes_a_forgotten_caller_visible() -> None:
    """`on_order` has no default, on `table` and on `get_by_code` alike.

    A default of `frozenset()` would turn "nobody asked procurement" into "nothing
    is on order" — silent, flattering, and exactly the ADR-0007 collapse this
    codebase keeps repeating. Required means mypy names every new call site
    instead.
    """
    for method in (InventoryService.table, InventoryService.get_by_code):
        parameter = inspect.signature(method).parameters["on_order"]
        assert parameter.default is inspect.Parameter.empty, method.__name__
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, method.__name__
