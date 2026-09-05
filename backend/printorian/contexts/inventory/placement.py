"""Where a physical thing is, and how it got there.

Kept out of `service.py` on purpose. That module is the materials *table* and the
recommendation — the catalogue question, "what should I print this out of". This
one is the warehouse question, "where is the spool and who moved it", and the two
grew apart the moment cells arrived. The same cut `intake.py` / `intake_routing.py`
took one context over, and the reason the 400-line gate is not the thing deciding
it.

Every writer here goes through :func:`record_movement`. One function, so "moved by
an operator" and "moved by mounting into a printer" cannot drift into meaning
different things — the arrangement `production/wait_list.py` already has between
the planner and the owner's manual clear.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.inventory.models import MaterialLot, StorageCell, StorageZone
from printorian.contexts.inventory.movements import (
    MOVED_MOVED,
    MOVED_WRITTEN_OFF,
    MaterialMovement,
)
from printorian.contexts.inventory.policies import LocationKind
from printorian.contexts.inventory.schemas import (
    CellView,
    CreateStorageCell,
    CreateStorageZone,
    LotView,
    ZoneView,
)
from printorian.core.errors import ConflictError, DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId


async def record_movement(
    session: AsyncSession,
    lot: MaterialLot,
    *,
    reason: str,
    at: datetime,
    grams: Decimal = Decimal(0),
    actor_id: EntityId | None = None,
    from_kind: LocationKind | None = None,
    from_address: str | None = None,
    to_kind: LocationKind | None = None,
    to_address: str | None = None,
    note: str | None = None,
) -> MaterialMovement:
    """Append the row for a movement that has just been decided.

    Added to the session and **not flushed**, exactly as `ordering.credit.record`
    is and for the same reason: the caller is changing the lot in the same unit of
    work, and the record of the change belongs in the same transaction as the
    change. A ledger that can commit without its effect — or an effect that can
    commit without its ledger — is worse than no ledger, because it is a record
    that looks complete.

    ``from_*`` is read off the lot by the caller *before* it overwrites anything.
    That ordering is the whole point of the module: the five location columns are
    the only place the previous position exists.
    """
    movement = MaterialMovement(
        lot_id=lot.id,
        sequence=await next_sequence(session, lot.id),
        reason=reason,
        grams=grams,
        remaining_after=lot.remaining_grams,
        at=at,
        actor_id=actor_id,
        from_kind=from_kind,
        from_address=from_address,
        to_kind=to_kind,
        to_address=to_address,
        note=note,
    )
    session.add(movement)
    return movement


async def next_sequence(session: AsyncSession, lot_id: EntityId) -> int:
    """Next position in this lot's history.

    ``MAX(sequence) + 1``, read off the unique index rather than counted: a count
    goes wrong the first time anything is rolled back, and then two rows claim the
    same position until the constraint refuses one of them.
    """
    highest = await session.scalar(
        select(func.max(MaterialMovement.sequence)).where(MaterialMovement.lot_id == lot_id)
    )
    return int(highest or 0) + 1


class PlacementService:
    """Declaring places, putting things in them, and taking mass off a reel."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # -- declaring places ------------------------------------------------

    async def create_zone(self, data: CreateStorageZone) -> ZoneView:
        existing = await self._db.scalar(select(StorageZone).where(StorageZone.code == data.code))
        if existing is not None:
            raise ConflictError("error.inventory.zone_exists", code=data.code)

        zone = StorageZone(**data.model_dump())
        self._db.add(zone)
        await self._db.flush()
        # A zone declared a moment ago has no cells, so its fill is `None` rather
        # than 0% — nothing has been declared there, which is not the same fact as
        # nothing being stored there (ADR-0007).
        return ZoneView(
            id=zone.id,
            code=zone.code,
            name=zone.name,
            temp_c=zone.temp_c,
            humidity_percent=zone.humidity_percent,
            cell_count=0,
            occupied_cells=0,
            fill_percent=None,
            cells=[],
        )

    async def create_cell(self, data: CreateStorageCell) -> CellView:
        zone = await self._db.scalar(select(StorageZone).where(StorageZone.code == data.zone_code))
        if zone is None:
            raise NotFoundError("error.inventory.zone_not_found", code=data.zone_code)

        clash = await self._db.scalar(
            select(StorageCell).where(StorageCell.address == data.address)
        )
        if clash is not None:
            # Refused before the flush so the caller gets the code rather than an
            # IntegrityError the handler would have to guess the meaning of.
            raise ConflictError("error.inventory.cell_address_exists", address=data.address)

        cell = StorageCell(zone_id=zone.id, address=data.address, capacity_lots=data.capacity_lots)
        self._db.add(cell)
        await self._db.flush()
        return cell_view(cell, zone_code=zone.code, lot_count=0)

    # -- moving things ---------------------------------------------------

    async def place_lot(
        self,
        lot_id: EntityId,
        *,
        address: str,
        at: datetime,
        actor_id: EntityId | None = None,
        note: str | None = None,
    ) -> LotView:
        """Put a lot into a cell, or move it between two, appending a movement."""
        lot = await self._lot(lot_id)
        cell = await self._db.scalar(
            select(StorageCell)
            .options(selectinload(StorageCell.zone))
            .where(StorageCell.address == address)
        )
        if cell is None:
            raise NotFoundError("error.inventory.cell_not_found", address=address)

        was = lot.cell_address
        if was == cell.address:
            # A ledger of non-movements is noise — the call `SlaCreditEntry` made
            # with its `credit_actually_moved` CHECK, made here in the service
            # because the two addresses being compared are not both on the row.
            raise DomainRuleViolationError("error.inventory.movement_not_a_move", address=address)

        occupied = await self._occupancy(cell.id)
        if cell.capacity_lots is not None and occupied >= cell.capacity_lots:
            # Only reachable when a capacity was declared. A null capacity never
            # refuses: "nobody has said how much fits" is not "it is full", and
            # treating the two alike would block the whole warehouse the day it is
            # first described (ADR-0007).
            raise DomainRuleViolationError(
                "error.inventory.cell_full",
                address=cell.address,
                capacity_lots=cell.capacity_lots,
                occupied=occupied,
            )

        await record_movement(
            self._db,
            lot,
            reason=MOVED_MOVED,
            at=at,
            actor_id=actor_id,
            from_kind=lot.location_kind,
            from_address=was,
            to_kind=LocationKind.STOCK,
            to_address=cell.address,
            note=note,
        )
        lot.cell_id = cell.id
        lot.location_kind = LocationKind.STOCK
        # A spool in a cell is not in a machine. Left set, the materials table
        # would go on offering it to the scheduler as loaded filament.
        lot.printer_id = None
        lot.ams_unit = None
        lot.ams_slot = None
        await self._db.flush()
        return LotView.model_validate(lot)

    async def write_off(
        self,
        lot_id: EntityId,
        *,
        grams: Decimal,
        at: datetime,
        actor_id: EntityId | None = None,
        note: str | None = None,
    ) -> LotView:
        """Take mass off a reel for good — the irreversible path.

        The bound is checked **before the row is touched**, which is the lesson
        `POST /jobs/{id}/plate/file?copies=0` taught by raising a bare pydantic 500
        after the file had already been stored: a refusal that happens after the
        write has changed something is not a refusal.

        This is the first and only code path in the system that decrements
        `remaining_grams`. Everything else reads it.
        """
        lot = await self._lot(lot_id)
        if grams > lot.remaining_grams:
            raise DomainRuleViolationError(
                "error.inventory.write_off_exceeds_remaining",
                requested_grams=str(grams),
                remaining_grams=str(lot.remaining_grams),
            )

        lot.remaining_grams = lot.remaining_grams - grams
        await record_movement(
            self._db,
            lot,
            reason=MOVED_WRITTEN_OFF,
            at=at,
            grams=grams,
            actor_id=actor_id,
            from_kind=lot.location_kind,
            from_address=lot.cell_address,
            # `CONSUMED` rather than a «брак» location: the mass has left the farm
            # and there is no place to point at. The reason column says which of
            # the ways it left this was.
            to_kind=LocationKind.CONSUMED,
            to_address=None,
            note=note,
        )
        await self._db.flush()
        return LotView.model_validate(lot)

    # -- internals -------------------------------------------------------

    async def _lot(self, lot_id: EntityId) -> MaterialLot:
        lot = await self._db.scalar(
            select(MaterialLot)
            .options(selectinload(MaterialLot.cell))
            .where(MaterialLot.id == lot_id)
        )
        if lot is None:
            raise NotFoundError("error.inventory.lot_not_found", lot_id=str(lot_id))
        return lot

    async def _occupancy(self, cell_id: EntityId) -> int:
        """How many live lots sit in this cell right now.

        Empty spools do not count against capacity: a reel with nothing on it is
        waste awaiting disposal, not stock, and the materials table already reads
        `remaining_grams > 0` as the definition of live.
        """
        count = await self._db.scalar(
            select(func.count())
            .select_from(MaterialLot)
            .where(MaterialLot.cell_id == cell_id, MaterialLot.remaining_grams > 0)
        )
        return int(count or 0)


def cell_view(cell: StorageCell, *, zone_code: str, lot_count: int) -> CellView:
    """One cell as the map draws it, with the fill rule applied in one place."""
    return CellView(
        id=cell.id,
        address=cell.address,
        zone_code=zone_code,
        capacity_lots=cell.capacity_lots,
        lot_count=lot_count,
        fill_percent=fill_percent(lot_count, cell.capacity_lots),
        is_active=cell.is_active,
    )


def fill_percent(occupied: int, capacity: int | None) -> Decimal | None:
    """How full, or ``None`` when the denominator was never observed.

    Both callers — the cell and the zone — come through here, so there is one
    place where "we were not told" is turned into an answer, and the answer is the
    absence of one. Returning 0 would draw an empty bar over four spools; returning
    100 is what a `capacity_lots` defaulted to 1 would have produced.
    """
    if capacity is None or capacity <= 0:
        return None
    return (Decimal(occupied) * 100 / Decimal(capacity)).quantize(Decimal("0.1"))


__all__ = [
    "PlacementService",
    "cell_view",
    "fill_percent",
    "next_sequence",
    "record_movement",
]
