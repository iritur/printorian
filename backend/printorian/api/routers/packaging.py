"""The packing bench over HTTP.

Two permissions, drawn where the responsibility actually splits. Moving a parcel
needs `PACK_ORDER`, which every operator has. Editing the tara catalogue or
publishing a new instruction needs `MANAGE_INVENTORY` — those decide what the
farm buys and what a packer is measured against, and neither is a shift-floor
call.

The board is one request for the reason the dashboard and the post-production
board are: the columns, the tiles, the tara table and the scorecards all describe
one instant, and a client fanning out would show a parcel in two columns at once.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from printorian.api.deps import (
    AppClock,
    CurrentActor,
    DbSession,
    Identity,
    Packaging,
    PackingShelf,
    requires,
)
from printorian.contexts.identity import Permission
from printorian.contexts.ordering import numbers_for
from printorian.contexts.ordering.models import OrderLine
from printorian.contexts.packaging import (
    ChooseTara,
    CreatePackTask,
    CreateTara,
    HoldParcel,
    PackBoard,
    PackColumn,
    PackLine,
    PackView,
    PublishInstruction,
    ReportDiscrepancy,
    TaraRow,
    TickStep,
    Weigh,
    board_columns,
    metrics,
    next_cutoff,
    pickups,
    scorecards,
    shift_kpi,
    tara_rows,
)
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/packaging",
    tags=["packaging"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)

_PACK = Depends(requires(Permission.PACK_ORDER))
_SHELF = Depends(requires(Permission.MANAGE_INVENTORY))


@router.get("/board")
async def board(db: DbSession, clock: AppClock, identity: Identity) -> PackBoard:
    """The whole screen, read against one instant."""
    now = clock.now()
    # One name lookup for the whole screen: the cards and the scorecards want the
    # same map, and asking twice would let the two disagree about somebody renamed
    # between the queries.
    names = await identity.display_names()
    return PackBoard(
        at=now,
        next_cutoff_at=await next_cutoff(db, now=now),
        columns=await _named_columns(db, now=now, names=names),
        kpi=await shift_kpi(db, now=now),
        tara=await tara_rows(db, now=now),
        metrics=await metrics(db, now=now),
        shift=await scorecards(db, now=now, names=names),
        pickups=await pickups(db, now=now),
    )


async def _named_columns(
    db: DbSession, *, now: datetime, names: dict[EntityId, str]
) -> list[PackColumn]:
    """The board, with orders named and their contents listed.

    Packing knows which order a parcel belongs to and who is on it — not what
    either is *called*, and not what the customer actually bought. Resolving all
    three is composition, and composition is this layer's job.
    """
    columns = await board_columns(db, now=now)
    cards = [card for column in columns for card in column.tasks]
    numbers = await numbers_for(db, [card.order_id for card in cards])
    contents = await _lines_for(db, [card.order_id for card in cards])
    for card in cards:
        card.order_number = numbers.get(card.order_id, "")
        card.lines = contents.get(card.order_id, [])
        if card.operator_id is not None:
            card.operator_name = names.get(card.operator_id, "")
    return columns


async def _lines_for(db: DbSession, order_ids: list[EntityId]) -> dict[EntityId, list[PackLine]]:
    """What each order contains, for the completeness check.

    Read from `ordering` rather than copied onto the parcel: the count a packer
    checks against has to be the count the customer bought, and two stored copies
    of it are two answers that can disagree.
    """
    if not order_ids:
        return {}
    rows = list(await db.scalars(select(OrderLine).where(OrderLine.order_id.in_(set(order_ids)))))
    found: dict[EntityId, list[PackLine]] = {}
    for line in rows:
        found.setdefault(line.order_id, []).append(
            PackLine(
                model_name=line.model_name,
                # One row per colour, because that is how a packer counts them —
                # "six black and four red", never "ten brackets".
                color=", ".join(line.colors),
                ordered=line.quantity,
                present=line.quantity,
            )
        )
    return found


# ------------------------------------------------------------------ the shift


@router.post("/parcels", status_code=status.HTTP_201_CREATED, dependencies=[_PACK])
async def raise_parcel(data: CreatePackTask, service: Packaging) -> PackView:
    """Raise a parcel by hand.

    Normally the sweep's job. Kept open to the floor for the order that reached
    the bench some other way — a walk-in collection, a reprint carried across.
    """
    return await service.raise_parcel(data)


@router.post("/parcels/{task_id}/start", dependencies=[_PACK])
async def start(task_id: EntityId, actor: CurrentActor, service: Packaging) -> PackView:
    """Pick a parcel up. The clock starts here.

    The packer is the caller, never a field in the body: a parcel attributed to
    somebody else is how a scorecard stops being a measurement.
    """
    return await service.start(task_id, actor.user_id)


@router.post("/parcels/{task_id}/steps", dependencies=[_PACK])
async def tick(task_id: EntityId, data: TickStep, service: Packaging) -> PackView:
    """Tick one step off. The last one closes the parcel by itself."""
    return await service.tick(task_id, data.position)


@router.post("/parcels/{task_id}/tara", dependencies=[_PACK])
async def choose_tara(task_id: EntityId, data: ChooseTara, service: Packaging) -> PackView:
    """Record what went into the parcel. Stock and cost move here."""
    return await service.choose_tara(task_id, data)


@router.post("/parcels/{task_id}/weight", dependencies=[_PACK])
async def weigh(task_id: EntityId, data: Weigh, service: Packaging) -> PackView:
    """What the scales said, beside the estimate rather than replacing it."""
    return await service.weigh(task_id, data)


@router.post("/parcels/{task_id}/ready", dependencies=[_PACK])
async def ready(task_id: EntityId, service: Packaging) -> PackView:
    """Sealed, weighed, labelled. Waiting for the van."""
    return await service.ready(task_id)


@router.post("/parcels/{task_id}/ship", dependencies=[_PACK])
async def ship(task_id: EntityId, service: Packaging) -> PackView:
    """Handed to the carrier."""
    return await service.ship(task_id)


@router.post("/parcels/{task_id}/hold", dependencies=[_PACK])
async def hold(task_id: EntityId, data: HoldParcel, service: Packaging) -> PackView:
    """Park a parcel on somebody else's problem, with the reason attached."""
    return await service.hold(task_id, data)


@router.post("/parcels/{task_id}/release", dependencies=[_PACK])
async def release(task_id: EntityId, service: Packaging) -> PackView:
    """Whatever blocked it is cleared. Back into the queue."""
    return await service.release(task_id)


@router.post("/parcels/{task_id}/discrepancy", dependencies=[_PACK])
async def report_discrepancy(
    task_id: EntityId, data: ReportDiscrepancy, service: Packaging
) -> PackView:
    """The count disagreed with the order. Holds the parcel where it stands.

    The code is required. A short parcel with no recorded reason is invisible to
    the «недовложений» figure the post is judged on, which is the same as not
    having recorded it.
    """
    return await service.report_discrepancy(task_id, data)


# ------------------------------------------------------------------ the shelf


@router.post("/tara", status_code=status.HTTP_201_CREATED, dependencies=[_SHELF])
async def stock_tara(data: CreateTara, shelf: PackingShelf) -> TaraRow:
    """Add a packing item, or restate one already on the shelf."""
    return await shelf.stock_tara(data)


@router.delete("/tara/{tara_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_SHELF])
async def retire_tara(tara_id: EntityId, shelf: PackingShelf) -> None:
    """Take a box out of the catalogue without erasing what shipped in it."""
    await shelf.retire_tara(tara_id)


@router.post("/instruction", status_code=status.HTTP_201_CREATED, dependencies=[_SHELF])
async def publish(data: PublishInstruction, shelf: PackingShelf) -> EntityId:
    """Put a new version of the packing instruction into service.

    Parcels already open keep the steps they were raised with — the whole reason
    those are copied — so this changes what the *next* parcel is worked to.
    """
    return await shelf.publish(data)
