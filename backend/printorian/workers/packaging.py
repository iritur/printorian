"""Turning inspected orders into parcels, and telling the order desk they shipped.

Reconciling rather than reactive, for the reason `workers/postproduction.py`
spells out at length: an event delivered while this process was restarting is
gone, and the parcel it was about sits uncreated while the van leaves. This pass
asks "which orders have finished every finishing task and have no parcel yet"
and makes the missing ones, so a missed tick costs latency and never a parcel.

It composes four contexts — ordering owns the order and its lines, catalog's mesh
analysis is what says a part is thin-walled, post-production owns the inspection
that gates it, packaging owns the parcel — which is exactly why it lives here and
not in any of them.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering.models import Order, OrderLine
from printorian.contexts.ordering.policies import DeliveryMethod
from printorian.contexts.packaging import (
    CreatePackTask,
    Dims,
    PackagingService,
    PackStatus,
    PackTask,
    batch_box,
    stack_box,
)
from printorian.contexts.postproduction import Task, TaskStatus
from printorian.core.clock import Clock
from printorian.core.errors import PrintorianError
from printorian.core.geometry import scaled_box
from printorian.core.ids import EntityId

logger = structlog.get_logger(__name__)

#: When each van goes, in the farm's own timezone.
#:
#: A stopgap, and deliberately a visible one. Carriers and their pickup windows
#: belong to `logistics`, and the hour itself belongs in settings — until those
#: exist, a parcel with no cutoff would give the board nothing to sort by and the
#: header nothing to count down to, which is worse than a stated default.
PICKUP_TIMES: dict[DeliveryMethod, time] = {
    DeliveryMethod.COURIER: time(19, 30),
    DeliveryMethod.FREIGHT: time(19, 30),
    # Collection runs later, because nobody has to drive anywhere with it.
    DeliveryMethod.PICKUP: time(21, 0),
}

#: The mesh warning that makes film mandatory. The catalogue already flags parts
#: whose approximate wall thickness is under 0.8 mm, which is the same threshold
#: `packaging.policies.THIN_WALL_MM` sets — one number, measured once, used twice.
THIN_WALL_WARNING = "warning.catalog.thin_walls"

#: Most orders one pass will convert. A larger backlog gets the rest moments later.
RAISE_BATCH = 100


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """What one pass did, for logging and the health endpoint."""

    #: Parcels created for orders that had none.
    raised: int = 0
    #: Orders that could not be converted. The rest of the pass still ran.
    failed: int = 0


class PackagingSweep:
    """One reconciling pass over inspected orders."""

    def __init__(self, db: AsyncSession, service: PackagingService, clock: Clock, tz: str) -> None:
        self._db = db
        self._service = service
        self._clock = clock
        self._tz = ZoneInfo(tz)

    async def sweep(self) -> SweepOutcome:
        raised = failed = 0
        for order_id in await self._ready_orders():
            try:
                raised += await self._raise_for(order_id)
            except PrintorianError as exc:
                # One order that cannot be converted must not stop the rest.
                failed += 1
                logger.warning("packaging.raise_failed", order_id=str(order_id), code=exc.code)
        return SweepOutcome(raised=raised, failed=failed)

    async def _ready_orders(self) -> list[EntityId]:
        """Orders whose every finishing task is done and which have no parcel.

        "Every task" matters: raising the parcel when the *first* one passes
        inspection would put a half-finished order on the packing bench, and the
        packer would find out by counting.
        """
        already = set(
            await self._db.scalars(
                select(PackTask.order_id).where(PackTask.status != PackStatus.CANCELLED)
            )
        )
        rows = (
            await self._db.execute(
                select(Task.order_id, Task.status).where(Task.status != TaskStatus.CANCELLED)
            )
        ).all()

        by_order: dict[EntityId, list[TaskStatus]] = {}
        for order_id, status in rows:
            by_order.setdefault(order_id, []).append(status)

        return [
            order_id
            for order_id, statuses in by_order.items()
            if order_id not in already and all(one is TaskStatus.DONE for one in statuses)
        ][:RAISE_BATCH]

    async def _raise_for(self, order_id: EntityId) -> int:
        order = await self._db.get(Order, order_id)
        if order is None:  # pragma: no cover — the FK on tasks prevents this
            return 0
        lines = list(
            await self._db.scalars(select(OrderLine).where(OrderLine.order_id == order.id))
        )
        method = DeliveryMethod(order.delivery_method)

        await self._service.raise_parcel(
            CreatePackTask(
                order_id=order.id,
                delivery_method=method.value,
                # No carrier until logistics can name one. Empty rather than a
                # guessed default: the board renders the method when there is no
                # carrier, and inventing "СДЭК" would be a claim, not a blank.
                carrier_code="",
                cutoff_at=self._next_pickup(method),
                items=sum(line.quantity for line in lines),
                estimated_grams=sum(
                    (line.estimated_grams * line.quantity for line in lines), Decimal(0)
                ),
                **_dimensions(lines),
                wrap_required=any(_is_thin(line) for line in lines),
            )
        )
        return 1

    def _next_pickup(self, method: DeliveryMethod) -> datetime:
        """The next time that van goes, from the farm's wall clock.

        Computed in the farm's timezone and returned in UTC, which is the rule
        everywhere: instants are stored and compared in UTC, and wall clocks exist
        only where a human said one out loud — and "the courier comes at half past
        seven" is exactly such a place.
        """
        local = self._clock.now().astimezone(self._tz)
        at = PICKUP_TIMES.get(method, time(19, 30))
        today = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        if today <= local:
            today += timedelta(days=1)
        return today.astimezone(UTC)


def _dimensions(lines: list[OrderLine]) -> dict[str, Decimal]:
    """The batch's bounding box, from the geometry each line was priced on."""
    stacks = [
        stack_box(dims, line.quantity)
        for line, dims in ((line, _line_dims(line)) for line in lines)
        if dims is not None
    ]
    box = batch_box(stacks)
    return {"length_mm": box.length_mm, "width_mm": box.width_mm, "height_mm": box.height_mm}


def _line_dims(line: OrderLine) -> Dims | None:
    """One part's bounding box **at the size it was ordered**, or ``None``.

    The box stored on the line is the box of the *unscaled* mesh, and this read it
    verbatim: a 100 mm part ordered at scale 3 was getting a carton recommended
    for 100 mm, which is exactly the failure the docstring below says a
    recommendation must not have. `core.geometry.scaled_box` is the multiplication,
    and it is shared with `workers/intake` rather than written twice — the second
    reader of an unscaled box was how this went unnoticed in the first place.

    An unmeasured line contributes nothing rather than a zero: a zero would shrink
    the batch and get a box recommended that the parcel does not fit in.
    """
    box = scaled_box(line.mesh, line.scale)
    return None if box is None else Dims(box.x, box.y, box.z)


def _is_thin(line: OrderLine) -> bool:
    """Whether the catalogue flagged this part's walls as too thin to travel bare."""
    warnings = line.mesh.get("warnings") if isinstance(line.mesh, dict) else None
    if not isinstance(warnings, list):
        return False
    return any(isinstance(one, dict) and one.get("code") == THIN_WALL_WARNING for one in warnings)


async def run_forever(
    build_sweep: Any,
    *,
    interval_seconds: int,
    stop: asyncio.Event | None = None,
) -> None:
    """Sweep on an interval until stopped.

    `build_sweep` returns a fresh sweep per pass, each with its own session and
    its own commit. Holding one across hours would keep a transaction open for the
    farm's lifetime and read a snapshot predating every inspection it should
    notice.
    """
    stop = stop or asyncio.Event()

    while not stop.is_set():
        try:
            sweep = await build_sweep()
            outcome = await sweep.sweep()
            if outcome.raised or outcome.failed:
                logger.info("packaging_sweep", raised=outcome.raised, failed=outcome.failed)
        except Exception:
            # A bad pass is logged and the loop continues: one failure must not
            # leave every inspected order invisible to the bench for ever after.
            logger.exception("packaging_sweep_failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


__all__ = [
    "PICKUP_TIMES",
    "RAISE_BATCH",
    "THIN_WALL_WARNING",
    "PackagingSweep",
    "SweepOutcome",
    "run_forever",
]
