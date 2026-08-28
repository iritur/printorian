"""The fleet: the printers table, service cards, and credentials.

Everything here needs a staff permission. Nothing here ever returns an access code
(ADR-0014) — views carry ``access_code_set`` and the code is set through its own
endpoint so an ordinary edit cannot blank it by omission.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from printorian.api.deps import DbSession, Fleet, requires
from printorian.contexts.fleet import (
    CreatePrinter,
    CreateServiceOperation,
    MountLot,
    PrinterTable,
    PrinterView,
    SetAccessCode,
)
from printorian.contexts.identity import Permission
from printorian.contexts.inventory import InventoryService
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/printers",
    tags=["printers"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)


@router.get("")
async def printers_table(fleet: Fleet, include_inactive: bool = False) -> PrinterTable:
    """Rows plus state counts — the scenario's printers screen (item M2).

    Each row carries its live state, progress and ETA where the machine reported
    them, and ``needs_attention`` so the floor can see at a glance what to walk to.

    **Unpaged, deliberately and temporarily.** A farm has tens of machines, so the
    whole table is a bounded response (`DATABASE-REVIEW` §9). `include_inactive`
    lifts the only filter, which makes this the largest response the endpoint can
    be asked for — and `contexts.fleet.listings` counts exactly that on every
    readiness probe, so `/health/ready` says `printers_listing: degraded` rather
    than leaving the moment to be noticed.
    """
    return await fleet.table(include_inactive=include_inactive)


@router.get("/{printer_id}")
async def get_printer(printer_id: EntityId, fleet: Fleet) -> PrinterView:
    """One machine in full: capability, AMS slots, service card, amortization."""
    return await fleet.get(printer_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires(Permission.MANAGE_FLEET))],
)
async def register_printer(data: CreatePrinter, fleet: Fleet) -> PrinterView:
    """Add a machine. Any access code supplied is encrypted before it is stored."""
    return await fleet.register(data)


@router.put(
    "/{printer_id}/access-code",
    dependencies=[Depends(requires(Permission.MANAGE_FLEET))],
)
async def set_access_code(printer_id: EntityId, data: SetAccessCode, fleet: Fleet) -> PrinterView:
    """Replace a printer's LAN credential.

    Write-only: the response says whether a code is now set, never what it is.
    """
    return await fleet.set_access_code(printer_id, data.access_code)


@router.put(
    "/{printer_id}/slots",
    dependencies=[Depends(requires(Permission.MANAGE_INVENTORY))],
)
async def mount_lot(
    printer_id: EntityId, data: MountLot, fleet: Fleet, db: DbSession
) -> PrinterView:
    """Record which physical material lot sits in which AMS slot.

    This is what gives the materials table its second location kind — "in printer,
    unit A, slot 3" — and what lets the scheduler know a colour is reachable.

    **Two contexts, one physical fact.** The fleet owns "what is in this slot";
    inventory owns "where is this spool". Moving a spool changes both, and the
    two must not be able to disagree — a materials table saying "shelf A1" for a
    spool that is loaded in a printer sends someone to an empty shelf. Neither
    context may import the other (ARCHITECTURE §layering), so composing them is
    the delivery layer's job, and it happens here in one transaction.
    """
    view = await fleet.mount_lot(printer_id, data)
    await InventoryService(db).mount_lot(
        data.lot_id,
        printer_id=printer_id,
        ams_unit=data.unit,
        ams_slot=data.index,
    )
    return view


@router.delete(
    "/{printer_id}/slots/{unit}/{index}",
    dependencies=[Depends(requires(Permission.MANAGE_INVENTORY))],
)
async def unmount_lot(
    printer_id: EntityId, unit: int, index: int, fleet: Fleet, db: DbSession, shelf: str = ""
) -> PrinterView:
    """Take the spool out of a slot and put it back into storage.

    Both halves again, for the same reason as mounting: without this a lot can
    enter a printer and never leave, so the materials table goes on showing
    filament in a machine it was pulled out of.

    ``shelf`` is where it physically went, and may be omitted — an operator who
    has not put it away yet records the removal now rather than leaving the
    system believing the spool is still loaded.
    """
    lot_id = await fleet.clear_slot(printer_id, unit=unit, index=index)
    if lot_id is not None:
        await InventoryService(db).unmount_lot(lot_id, shelf=shelf or None)
    return await fleet.get(printer_id)


@router.post(
    "/{printer_id}/services",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires(Permission.MANAGE_FLEET))],
)
async def add_service_operation(
    printer_id: EntityId, data: CreateServiceOperation, fleet: Fleet
) -> PrinterView:
    """Add an operation to the service card, with its periodicity."""
    return await fleet.add_service_operation(printer_id, data)


@router.post("/{printer_id}/services/{operation_id}/complete")
async def complete_service(
    printer_id: EntityId, operation_id: EntityId, fleet: Fleet
) -> PrinterView:
    """Mark a service done, resetting its clock to the machine's printing hours.

    Open to operators: the person who changed the nozzle is the person who should
    record it, and making them find a manager first is how service logs stop being
    kept.
    """
    return await fleet.complete_service(printer_id, operation_id)
