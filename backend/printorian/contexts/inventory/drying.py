"""Whether a spool is dry enough to print from, and the two writes that change it.

Issue #35's third slice. `inventory.require_drying` and `inventory.drying_valid_hours`
have been in the settings catalogue since the screen was built, and until this
module nothing read either — a settings row nothing reads is the failure mode
CLAUDE.md §1 warns about wearing a nicer hat, and #29 says so in as many words.
This is the read.

**The state is derived, never stored.** `MaterialLot` carries one fact —
`dried_at`, the moment the spool last came out of a dryer — and the state is
computed from that fact, the clock and the policy at the moment somebody asks.
Storing «просрочена» would need a sweep to flip it, and a spool nobody looked at
for a week would read as dry the whole week. `MaterialStatus` made the same call
against V1's stored status field.

**Five states, and the difference between two of them is the ADR-0007 decision
here.** A spool that was *never* marked dried is `unknown`, not `expired`: the
farm has not measured it, which is a different fact from having measured it and
let the mark lapse. The kit draws the second as «просрочена» and the first as a
dash, and the console keeps them apart.

**Which materials it applies to.** The setting's own hint names PA, PETG-CF and
TPU — the hygroscopic families — so `needs_drying` is a family test, not a
per-spec flag. A flag on `MaterialSpec` that nothing sets would leave every
material reading «не требуется» and the whole panel silent; a family set here is
one line to extend and visible in the diff when it is. A family not in the set
answers `not_required`, which is honest for PLA and is the state that lets a
farm running only PLA see nothing change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.inventory.movements import MOVED_DRIED, MOVED_TO_DRYER
from printorian.contexts.inventory.placement import record_movement
from printorian.contexts.inventory.policies import DryingState, LocationKind
from printorian.contexts.inventory.schemas import DryingView, LotView
from printorian.core.errors import DomainRuleViolationError, NotFoundError
from printorian.core.ids import EntityId

#: Families whose filament takes on water on the shelf. The three the setting's
#: hint names, plus the nylon and PVA spellings the catalogue seeds use. Matched
#: on the family code exactly, upper-cased — a spec's `family` is a short code,
#: not prose.
HYGROSCOPIC_FAMILIES: frozenset[str] = frozenset(
    {"PA", "PA-CF", "PA-GF", "PA6", "PA12", "NYLON", "PETG-CF", "TPU", "PVA"}
)

_HOUR_PLACES = Decimal("0.1")


def needs_drying(family: str) -> bool:
    """Whether a spool of this family is one the drying rule is about."""
    return family.strip().upper() in HYGROSCOPIC_FAMILIES


@dataclass(frozen=True, slots=True, kw_only=True)
class DryingPolicy:
    """The two settings, resolved. Frozen so a read edge cannot edit it in passing."""

    required: bool = True
    valid_hours: int = 72


def drying_of(
    *,
    family: str,
    location_kind: LocationKind,
    dried_at: datetime | None,
    now: datetime,
    policy: DryingPolicy,
) -> DryingView:
    """The state of one spool right now, from the one stored fact and the clock.

    Order matters and is the order a person would ask: does the rule apply at
    all; is it in the dryer this minute; has it ever been dried; is the mark
    still good. ``hours_left`` is set only in the `dry` state, and it is hours
    *remaining* rather than hours elapsed, because the question at the shelf is
    "can I still print from this" and not "when did somebody dry it".
    """
    if not policy.required or not needs_drying(family):
        return DryingView(state=DryingState.NOT_REQUIRED, dried_at=dried_at)
    if location_kind is LocationKind.DRYER:
        return DryingView(state=DryingState.DRYING, dried_at=dried_at)
    if dried_at is None:
        # Never marked. Not "expired": nothing was measured, so nothing lapsed.
        return DryingView(state=DryingState.UNKNOWN, dried_at=None)
    valid_until = dried_at + timedelta(hours=policy.valid_hours)
    if now >= valid_until:
        return DryingView(state=DryingState.EXPIRED, dried_at=dried_at, valid_until=valid_until)
    hours_left = (Decimal((valid_until - now).total_seconds()) / Decimal(3600)).quantize(
        _HOUR_PLACES
    )
    return DryingView(
        state=DryingState.DRY, dried_at=dried_at, valid_until=valid_until, hours_left=hours_left
    )


class DryingService:
    """Sending a spool to the dryer and taking it back out.

    Both are movements, appended through `record_movement` like every other
    change of place in this context, so «Основание» on the store screen shows
    them and a lot's history says when it was dried without a second table. The
    cell is **kept** while the spool is in the dryer: it is coming back to the
    same place, the kit's cell panel lists it there with «На сушке», and clearing
    the cell would make the map show a free slot somebody would then fill.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def send_to_dryer(
        self,
        lot_id: EntityId,
        *,
        at: datetime,
        actor_id: EntityId | None = None,
        note: str | None = None,
    ) -> LotView:
        lot = await self._lot(lot_id)
        if lot.location_kind is LocationKind.DRYER:
            raise DomainRuleViolationError("error.inventory.lot_already_drying")
        if lot.location_kind is not LocationKind.STOCK:
            # A spool in a machine is unmounted first, through the path that
            # records which machine it left; a consumed one has no filament to dry.
            raise DomainRuleViolationError(
                "error.inventory.lot_not_in_stock", location_kind=str(lot.location_kind)
            )
        await record_movement(
            self._db,
            lot,
            reason=MOVED_TO_DRYER,
            at=at,
            actor_id=actor_id,
            from_kind=lot.location_kind,
            from_address=lot.cell_address,
            to_kind=LocationKind.DRYER,
            # The dryer is not a cell and has no address the ledger could copy.
            to_address=None,
            note=note,
        )
        lot.location_kind = LocationKind.DRYER
        await self._db.flush()
        return LotView.model_validate(lot)

    async def mark_dried(
        self,
        lot_id: EntityId,
        *,
        at: datetime,
        actor_id: EntityId | None = None,
        note: str | None = None,
    ) -> LotView:
        """Back on the shelf with a fresh mark. Only from the dryer.

        Refusing a spool that was never sent is what keeps `dried_at` meaning
        "came out of a dryer" rather than "somebody pressed the button". The
        validity window is not stored: it is the setting's value *at read time*,
        so a farm that shortens the window sees every mark shorten with it,
        which is what changing the rule should do.
        """
        lot = await self._lot(lot_id)
        if lot.location_kind is not LocationKind.DRYER:
            raise DomainRuleViolationError(
                "error.inventory.lot_not_drying", location_kind=str(lot.location_kind)
            )
        await record_movement(
            self._db,
            lot,
            reason=MOVED_DRIED,
            at=at,
            actor_id=actor_id,
            from_kind=LocationKind.DRYER,
            from_address=None,
            to_kind=LocationKind.STOCK,
            to_address=lot.cell_address,
            note=note,
        )
        lot.location_kind = LocationKind.STOCK
        lot.dried_at = at
        await self._db.flush()
        return LotView.model_validate(lot)

    async def _lot(self, lot_id: EntityId) -> MaterialLot:
        lot = await self._db.scalar(
            select(MaterialLot)
            .options(selectinload(MaterialLot.cell))
            .where(MaterialLot.id == lot_id)
        )
        if lot is None:
            raise NotFoundError("error.inventory.lot_not_found", lot_id=str(lot_id))
        return lot


__all__ = ["HYGROSCOPIC_FAMILIES", "DryingPolicy", "DryingService", "drying_of", "needs_drying"]
