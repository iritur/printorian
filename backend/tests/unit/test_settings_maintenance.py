"""«Периодичность по умолчанию» as a settings row: filed, parsed, resolved, and read.

Issue #29's maintenance-intervals table. The load-bearing tests are the last
two: the table has to be what a new machine's service card is seeded from, and
what an operation added without a periodicity takes — a settings row nothing
reads is a number the owner set and the farm ignored (CLAUDE.md §1).

Its own file rather than more of `test_settings_zones.py`, for the reason that
file gives about `test_settings_catalogue.py`: each is near the 400-line gate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import (
    ConnectionMode,
    CreatePrinter,
    FleetService,
    MaintenanceDefault,
    MaintenanceDefaults,
    MaintenanceKind,
    default_maintenance,
    seed_service_card,
)
from printorian.contexts.fleet.models import ServiceOperation
from printorian.contexts.settings import FIELDS, SECTIONS, Kind, SettingsService
from printorian.core.clock import FixedClock
from printorian.core.errors import ValidationError
from printorian.core.events import EventBus
from printorian.core.secrets import SecretBox

KEY = "service.maintenance_defaults"
ALL_KINDS = [kind.value for kind in MaintenanceKind]


def store(db: AsyncSession, clock: FixedClock) -> SettingsService:
    return SettingsService(db, clock)


# ------------------------------------------------------------ the catalogue


def test_the_table_is_filed_under_service_as_its_own_panel() -> None:
    spec = FIELDS[KEY]
    assert spec.section == "service"
    assert spec.kind is Kind.TABLE
    # No group: the editor is the panel and heads itself, like the zones table.
    assert spec.group is None
    assert KEY in next(section for section in SECTIONS if section.id == "service").fields


def test_the_default_is_every_kind_at_the_five_hundred_hours_the_code_always_assumed() -> None:
    """An untouched table changes nothing: the context's rule, and the number
    `CreateServiceOperation` defaulted to before the table existed."""
    default = FIELDS[KEY].default
    assert default == default_maintenance()
    assert [row.kind.value for row in default.rows] == ALL_KINDS
    assert {row.interval_hours for row in default.rows} == {500}


# ------------------------------------------------------------ parsing


async def test_a_well_formed_table_round_trips(db_session: AsyncSession, clock: FixedClock) -> None:
    settings = store(db_session, clock)
    rows = [
        {"code": "nozzle_change", "interval_hours": 300},
        {"code": "lubrication", "interval_hours": 800},
    ]

    await settings.set_value(KEY, rows, by=None)

    listing = {row.key: row for row in await settings.listing()}
    assert listing[KEY].value == rows
    assert listing[KEY].is_overridden


async def test_something_that_is_not_a_list_is_refused_as_a_table(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(KEY, {"code": "nozzle_change"}, by=None)
    assert raised.value.code == "error.settings.not_a_table"
    assert raised.value.details["key"] == KEY


async def test_a_row_missing_its_interval_is_refused_rather_than_defaulted(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(KEY, [{"code": "nozzle_change"}], by=None)
    assert raised.value.code == "error.settings.not_a_table"


async def test_a_kind_the_fleet_has_never_heard_of_is_named_in_the_refusal(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """The fleet's own code, with the known kinds beside it, so the screen can say
    which row and what it may become (ADR-0012)."""
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(
            KEY, [{"code": "oil_change", "interval_hours": 100}], by=None
        )
    assert raised.value.code == "error.fleet.maintenance_kind_unknown"
    assert raised.value.details["code"] == "oil_change"
    assert raised.value.details["known"] == ALL_KINDS


async def test_two_rows_for_one_kind_are_refused(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(
            KEY,
            [
                {"code": "nozzle_change", "interval_hours": 300},
                {"code": "nozzle_change", "interval_hours": 400},
            ],
            by=None,
        )
    assert raised.value.code == "error.fleet.maintenance_kind_duplicate"
    assert raised.value.details["codes"] == ["nozzle_change"]


@pytest.mark.parametrize("hours", [0, -5])
async def test_an_interval_that_is_not_one_is_refused(
    db_session: AsyncSession, clock: FixedClock, hours: int
) -> None:
    """Zero would put an operation permanently overdue on every new machine."""
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(
            KEY, [{"code": "nozzle_change", "interval_hours": hours}], by=None
        )
    assert raised.value.code == "error.fleet.maintenance_interval_invalid"
    assert raised.value.details["code"] == "nozzle_change"


async def test_a_boolean_is_not_an_interval(db_session: AsyncSession, clock: FixedClock) -> None:
    """`True` is an `int` in Python and `1` hour is not what anybody meant."""
    with pytest.raises(ValidationError) as raised:
        await store(db_session, clock).set_value(
            KEY, [{"code": "nozzle_change", "interval_hours": True}], by=None
        )
    assert raised.value.code == "error.settings.not_a_table"


# ------------------------------------------------------------ the read edge


async def test_an_unset_table_resolves_to_the_code_default(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    assert await store(db_session, clock).resolve_maintenance_defaults() == default_maintenance()


async def test_the_stored_table_is_what_resolves(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    settings = store(db_session, clock)
    await settings.set_value(KEY, [{"code": "deep_clean", "interval_hours": 1200}], by=None)

    resolved = await settings.resolve_maintenance_defaults()

    assert resolved == MaintenanceDefaults(
        rows=(MaintenanceDefault(kind=MaintenanceKind.DEEP_CLEAN, interval_hours=1200),)
    )
    assert resolved.hours_for(MaintenanceKind.DEEP_CLEAN) == 1200
    # A kind the table does not carry is not seeded and is not defaulted here:
    # the caller decides, and says so.
    assert resolved.hours_for(MaintenanceKind.NOZZLE_CHANGE) is None


# ------------------------------------------------------------ the consumer


@pytest.fixture
def fleet(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> FleetService:
    return FleetService(
        db_session, clock, bus, SecretBox("a-development-secret-key-not-for-production")
    )


async def test_seeding_gives_a_machine_one_operation_per_row_with_its_clock_set_to_now(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    printer = await fleet.register(
        CreatePrinter(name="X1C-07", model="Bambu X1C", connection_mode=ConnectionMode.MANUAL)
    )
    defaults = MaintenanceDefaults(
        rows=(
            MaintenanceDefault(kind=MaintenanceKind.NOZZLE_CHANGE, interval_hours=300),
            MaintenanceDefault(kind=MaintenanceKind.LUBRICATION, interval_hours=800),
        )
    )

    added = await seed_service_card(
        db_session, printer_id=printer.id, printed_hours=Decimal("12.5"), defaults=defaults
    )

    rows = list(
        await db_session.scalars(
            select(ServiceOperation).where(ServiceOperation.printer_id == printer.id)
        )
    )
    assert added == 2
    assert {(row.kind, row.interval_hours) for row in rows} == {
        (MaintenanceKind.NOZZLE_CHANGE, 300),
        (MaintenanceKind.LUBRICATION, 800),
    }
    # The clock starts at the machine's hours, not at zero: a used machine
    # registered today is not owed a service it already had.
    assert {row.last_done_at_hours for row in rows} == {Decimal("12.5")}


async def test_seeding_is_additive_over_a_card_an_engineer_already_started(
    fleet: FleetService, db_session: AsyncSession
) -> None:
    """A kind already on the card is left alone rather than duplicated."""
    printer = await fleet.register(
        CreatePrinter(name="X1C-08", model="Bambu X1C", connection_mode=ConnectionMode.MANUAL)
    )
    defaults = MaintenanceDefaults(
        rows=(
            MaintenanceDefault(kind=MaintenanceKind.NOZZLE_CHANGE, interval_hours=300),
            MaintenanceDefault(kind=MaintenanceKind.LUBRICATION, interval_hours=800),
        )
    )

    added = await seed_service_card(
        db_session,
        printer_id=printer.id,
        printed_hours=Decimal(0),
        defaults=defaults,
        existing=[MaintenanceKind.NOZZLE_CHANGE],
    )

    kinds = list(
        await db_session.scalars(
            select(ServiceOperation.kind).where(ServiceOperation.printer_id == printer.id)
        )
    )
    assert added == 1
    assert kinds == [MaintenanceKind.LUBRICATION]
