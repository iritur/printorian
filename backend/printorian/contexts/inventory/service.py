"""Inventory use cases: the materials table, and scenario-based recommendation."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.contexts.inventory.policies import (
    LocationKind,
    MaterialStatus,
    derive_status,
)
from printorian.contexts.inventory.schemas import (
    CreateMaterialLot,
    CreateMaterialSpec,
    LotView,
    MaterialSpecView,
    MaterialTable,
    ScenarioMatch,
    StatusCount,
)
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.ids import EntityId

#: Scoring weights for scenario matching. Availability beats specification: a
#: perfect material nobody has in stock is a worse recommendation than a good one
#: sitting in an AMS slot right now.
_SCORE_IN_PRINTER = 40
_SCORE_IN_STOCK = 25
_SCORE_PROPERTY_MET = 15
_SCORE_FAMILY_PREFERRED = 10


class InventoryService:
    """Materials, their physical lots, and what to make things out of."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # -- the table -------------------------------------------------------

    async def table(self, *, family: str | None = None) -> MaterialTable:
        """Rows plus status counts, as the scenario's materials screen needs."""
        query = (
            select(MaterialSpec)
            .options(selectinload(MaterialSpec.lots))
            .where(MaterialSpec.is_active.is_(True))
            .order_by(MaterialSpec.family, MaterialSpec.name)
        )
        if family:
            query = query.where(MaterialSpec.family == family)

        specs = list(await self._db.scalars(query))
        rows = [self._to_view(spec) for spec in specs]

        tally = Counter(row.status for row in rows)
        # Every status appears, including the empty ones: a chip reading "ordered 0"
        # is information, while a missing chip is just a gap.
        counts = [
            StatusCount(status=status, count=tally.get(status, 0)) for status in MaterialStatus
        ]
        return MaterialTable(rows=rows, counts=counts, total=len(rows))

    async def get_by_code(self, code: str) -> MaterialSpecView:
        spec = await self._db.scalar(
            select(MaterialSpec)
            .options(selectinload(MaterialSpec.lots))
            .where(MaterialSpec.code == code)
        )
        if spec is None:
            raise NotFoundError("error.inventory.spec_not_found", material_code=code)
        return self._to_view(spec)

    # -- writes ----------------------------------------------------------

    async def create_spec(self, data: CreateMaterialSpec) -> MaterialSpecView:
        existing = await self._db.scalar(select(MaterialSpec).where(MaterialSpec.code == data.code))
        if existing is not None:
            raise ConflictError("error.inventory.spec_exists", material_code=data.code)

        spec = MaterialSpec(**data.model_dump())
        self._db.add(spec)
        await self._db.flush()
        await self._db.refresh(spec, ["lots"])
        return self._to_view(spec)

    async def add_lot(self, data: CreateMaterialLot) -> LotView:
        # `lots` is eager-loaded because the default label counts existing lots;
        # touching a lazy relationship under asyncio raises MissingGreenlet.
        spec = await self._db.scalar(
            select(MaterialSpec)
            .options(selectinload(MaterialSpec.lots))
            .where(MaterialSpec.code == data.spec_code)
        )
        if spec is None:
            raise NotFoundError("error.inventory.spec_not_found", material_code=data.spec_code)

        lot = MaterialLot(
            spec_id=spec.id,
            label=data.label or f"{spec.code}-{len(spec.lots) + 1:03d}",
            initial_grams=data.initial_grams,
            remaining_grams=(
                data.initial_grams if data.remaining_grams is None else data.remaining_grams
            ),
            location_kind=LocationKind.STOCK,
            shelf=data.shelf,
        )
        self._db.add(lot)
        await self._db.flush()
        return LotView.model_validate(lot)

    async def mount_lot(
        self, lot_id: object, *, printer_id: EntityId, ams_unit: int, ams_slot: int
    ) -> LotView:
        """Move a lot into a printer's AMS slot — the scenario's second location kind."""
        lot = await self._db.get(MaterialLot, lot_id)
        if lot is None:
            raise NotFoundError("error.inventory.lot_not_found")

        lot.location_kind = LocationKind.PRINTER
        lot.printer_id = printer_id
        lot.ams_unit = ams_unit
        lot.ams_slot = ams_slot
        lot.shelf = None
        await self._db.flush()
        return LotView.model_validate(lot)

    async def unmount_lot(self, lot_id: object, *, shelf: str | None = None) -> LotView:
        """Take a lot out of a printer and put it back into storage.

        The counterpart to :meth:`mount_lot`. Without it a spool can enter a
        printer and never leave except by being consumed, which leaves the
        materials table showing filament in a machine it was removed from days
        ago — and the scheduler believing a colour is loaded when it is not.

        ``shelf`` is where it physically went. It is optional because an operator
        pulling a spool mid-shift may not know yet; the lot is then in stock
        without a recorded place, which is honest, rather than being left in a
        printer it is not in.
        """
        lot = await self._db.get(MaterialLot, lot_id)
        if lot is None:
            raise NotFoundError("error.inventory.lot_not_found")

        lot.location_kind = LocationKind.STOCK
        lot.printer_id = None
        lot.ams_unit = None
        lot.ams_slot = None
        lot.shelf = shelf
        await self._db.flush()
        return LotView.model_validate(lot)

    # -- recommendation --------------------------------------------------

    async def recommend(
        self,
        *,
        min_tensile_mpa: Decimal | None = None,
        min_hdt_c: Decimal | None = None,
        requires_flexible: bool = False,
        requires_outdoor: bool = False,
        preferred_families: tuple[str, ...] = (),
        limit: int = 5,
    ) -> list[ScenarioMatch]:
        """Pick materials for a usage scenario (scenario option 2a).

        Hard requirements filter; everything else scores. Availability is weighted
        above specification, because the recommendation has to be printable today.
        """
        table = await self.table()
        matches: list[ScenarioMatch] = []

        for row in table.rows:
            if requires_flexible and not row.is_flexible:
                continue
            if requires_outdoor and not row.is_outdoor_safe:
                continue
            if min_tensile_mpa is not None and (row.tensile_mpa or Decimal(0)) < min_tensile_mpa:
                continue
            if min_hdt_c is not None and (row.hdt_c or Decimal(0)) < min_hdt_c:
                continue

            score = 0
            reasons: list[str] = []
            if row.status is MaterialStatus.IN_PRINTER:
                score += _SCORE_IN_PRINTER
                reasons.append("match.loaded_in_printer")
            elif row.status is MaterialStatus.STOCK:
                score += _SCORE_IN_STOCK
                reasons.append("match.in_stock")

            if min_tensile_mpa is not None:
                score += _SCORE_PROPERTY_MET
                reasons.append("match.tensile")
            if min_hdt_c is not None:
                score += _SCORE_PROPERTY_MET
                reasons.append("match.heat_resistance")
            if requires_flexible:
                reasons.append("match.flexible")
            if requires_outdoor:
                reasons.append("match.outdoor")
            if row.family in preferred_families:
                score += _SCORE_FAMILY_PREFERRED
                reasons.append("match.preferred_family")

            matches.append(ScenarioMatch(spec=row, score=score, reasons=reasons))

        matches.sort(key=lambda match: (-match.score, match.spec.sell_price_per_gram))
        return matches[:limit]

    # -- internals -------------------------------------------------------

    @staticmethod
    def _to_view(spec: MaterialSpec) -> MaterialSpecView:
        live = [lot for lot in spec.lots if lot.remaining_grams > 0]
        status = derive_status(
            has_stock_lots=any(lot.location_kind is LocationKind.STOCK for lot in live),
            has_mounted_lots=any(lot.location_kind is LocationKind.PRINTER for lot in live),
            has_open_orders=spec.has_open_order,
        )
        return MaterialSpecView(
            id=spec.id,
            code=spec.code,
            name=spec.name,
            family=spec.family,
            color_name=spec.color_name,
            color_hex=spec.color_hex,
            density_g_per_cm3=spec.density_g_per_cm3,
            sell_price_per_gram=spec.sell_price_per_gram,
            purchase_price_per_1000m=spec.purchase_price_per_1000m,
            tensile_mpa=spec.tensile_mpa,
            hdt_c=spec.hdt_c,
            is_flexible=spec.is_flexible,
            is_outdoor_safe=spec.is_outdoor_safe,
            status=status,
            total_remaining_grams=sum((lot.remaining_grams for lot in live), start=Decimal(0)),
            lot_count=len(live),
            lots=[LotView.model_validate(lot) for lot in live],
        )
