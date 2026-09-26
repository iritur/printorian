"""The drying state, derived from one instant and the clock.

Issue #35's third slice. The rules that make the figure honest:

* **never marked is `unknown`, not `expired`** — nothing was measured, so nothing
  lapsed, and the console draws the two differently;
* **the window is the setting's value at read time**, so shortening it on the
  settings screen shortens every mark, and a farm that switches the rule off sees
  every spool read `not_required`;
* **the two writes are ledger rows**, and only the one out of the dryer touches
  `dried_at`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import (
    MOVED_DRIED,
    MOVED_TO_DRYER,
    DryingPolicy,
    DryingService,
    DryingState,
    LocationKind,
    StoreViews,
    drying_of,
    needs_drying,
)
from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.contexts.inventory.movements import MaterialMovement
from printorian.contexts.inventory.placement import PlacementService
from printorian.contexts.inventory.schemas import CreateStorageCell, CreateStorageZone
from printorian.core.errors import DomainRuleViolationError
from printorian.core.ids import new_id
from tests.factories import ensure_lot

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
POLICY = DryingPolicy(required=True, valid_hours=72)


def state(
    *,
    family: str = "PA",
    location_kind: LocationKind = LocationKind.STOCK,
    dried_at: datetime | None = None,
    policy: DryingPolicy = POLICY,
) -> DryingState:
    return drying_of(
        family=family, location_kind=location_kind, dried_at=dried_at, now=T0, policy=policy
    ).state


# ------------------------------------------------------------ the fold


def test_a_spool_never_marked_is_unknown_and_not_expired() -> None:
    view = drying_of(
        family="PA", location_kind=LocationKind.STOCK, dried_at=None, now=T0, policy=POLICY
    )
    assert view.state is DryingState.UNKNOWN
    # Nothing to count down from: no window is invented for a mark that was never made.
    assert view.valid_until is None
    assert view.hours_left is None


def test_the_mark_is_good_for_the_window_and_then_lapses() -> None:
    dried = T0 - timedelta(hours=30)
    fresh = drying_of(
        family="PA", location_kind=LocationKind.STOCK, dried_at=dried, now=T0, policy=POLICY
    )
    assert fresh.state is DryingState.DRY
    assert fresh.valid_until == dried + timedelta(hours=72)
    assert fresh.hours_left == Decimal("42.0")

    # The boundary itself is lapsed: at exactly 72 hours the mark is no longer good.
    assert state(dried_at=T0 - timedelta(hours=72)) is DryingState.EXPIRED
    assert state(dried_at=T0 - timedelta(hours=71, minutes=59)) is DryingState.DRY


def test_the_window_is_the_policy_at_read_time() -> None:
    dried = T0 - timedelta(hours=30)
    assert state(dried_at=dried, policy=DryingPolicy(valid_hours=24)) is DryingState.EXPIRED
    assert state(dried_at=dried, policy=DryingPolicy(valid_hours=48)) is DryingState.DRY


def test_the_rule_is_off_for_families_that_do_not_take_on_water_and_when_switched_off() -> None:
    assert needs_drying("PA")
    assert needs_drying("petg-cf")
    assert not needs_drying("PLA")
    assert state(family="PLA", dried_at=T0 - timedelta(days=30)) is DryingState.NOT_REQUIRED
    assert state(policy=DryingPolicy(required=False)) is DryingState.NOT_REQUIRED


def test_a_spool_in_the_dryer_is_drying_whatever_its_old_mark_said() -> None:
    assert state(location_kind=LocationKind.DRYER) is DryingState.DRYING
    assert (
        state(location_kind=LocationKind.DRYER, dried_at=T0 - timedelta(days=30))
        is DryingState.DRYING
    )


# ------------------------------------------------------------ the writes


async def _a_pa_lot_in_a_cell(db: AsyncSession) -> MaterialLot:
    lot_id = new_id()
    await ensure_lot(db, lot_id, code="PA-TEST")
    lot = await db.get(MaterialLot, lot_id)
    assert lot is not None
    spec = await db.get(MaterialSpec, lot.spec_id)
    assert spec is not None
    spec.family = "PA"
    placement = PlacementService(db)
    await placement.create_zone(CreateStorageZone(code="D", name="Сухой шкаф"))
    await placement.create_cell(CreateStorageCell(zone_code="D", address="D1-1"))
    await placement.place_lot(lot_id, address="D1-1", at=T0 - timedelta(days=1))
    return lot


async def test_out_of_the_dryer_writes_the_mark_and_both_ways_are_ledger_rows(
    db_session: AsyncSession,
) -> None:
    lot = await _a_pa_lot_in_a_cell(db_session)
    drying = DryingService(db_session)

    await drying.send_to_dryer(lot.id, at=T0)
    assert lot.location_kind is LocationKind.DRYER
    # The cell is kept: the spool is coming back to the same place, and the map
    # must not offer its slot to somebody else meanwhile.
    assert lot.cell_address == "D1-1"
    assert lot.dried_at is None

    await drying.mark_dried(lot.id, at=T0 + timedelta(hours=8))
    assert lot.location_kind is LocationKind.STOCK
    assert lot.dried_at == T0 + timedelta(hours=8)

    rows = list(
        await db_session.scalars(
            select(MaterialMovement)
            .where(MaterialMovement.lot_id == lot.id)
            .order_by(MaterialMovement.sequence)
        )
    )
    assert [row.reason for row in rows][-2:] == [MOVED_TO_DRYER, MOVED_DRIED]
    assert (rows[-2].from_address, rows[-2].to_kind) == ("D1-1", LocationKind.DRYER)
    assert (rows[-1].from_kind, rows[-1].to_address) == (LocationKind.DRYER, "D1-1")

    detail = await StoreViews(db_session).cell_detail(
        "D1-1", now=T0 + timedelta(hours=20), drying=POLICY
    )
    [stored] = detail.lots
    assert stored.drying.state is DryingState.DRY
    assert stored.drying.hours_left == Decimal("60.0")
    assert detail.drying_valid_hours == 72


async def test_marking_a_spool_that_was_never_sent_is_refused(db_session: AsyncSession) -> None:
    """Otherwise `dried_at` means "somebody pressed the button", not "came out of a dryer"."""
    lot = await _a_pa_lot_in_a_cell(db_session)
    with pytest.raises(DomainRuleViolationError) as raised:
        await DryingService(db_session).mark_dried(lot.id, at=T0)
    assert raised.value.code == "error.inventory.lot_not_drying"
    assert lot.dried_at is None

    await DryingService(db_session).send_to_dryer(lot.id, at=T0)
    with pytest.raises(DomainRuleViolationError) as again:
        await DryingService(db_session).send_to_dryer(lot.id, at=T0)
    assert again.value.code == "error.inventory.lot_already_drying"
