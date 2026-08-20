"""The dashboard's floor reads: filament, the schedule strip, and throughput.

Three claims, each about a figure an operator acts on:

* the committed column counts work that has not been sliced yet, because the
  material is spoken for the moment the order exists;
* a running job's bar shrinks as the machine works, rather than restating the
  whole job on every refresh;
* a farm that printed nothing has *no* success rate, rather than a perfect one.

The commercial half is in `test_dashboard_reads.py`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.inventory import headroom
from printorian.contexts.inventory.models import MaterialLot
from printorian.contexts.inventory.policies import LocationKind
from printorian.contexts.production import committed_material, hourly_load, schedule, throughput
from printorian.contexts.production.policies import JobStatus
from tests.unit._dashboard_support import NOW, a_job, a_material, a_printer, an_order_id

# ------------------------------------------------------------------ filament


async def test_headroom_splits_a_material_between_machine_and_shelf(
    db_session: AsyncSession,
) -> None:
    spec = await a_material(db_session, code="PETG-CF-BLACK")
    printer_id = await a_printer(db_session, name="P-01")
    db_session.add_all(
        [
            MaterialLot(
                spec_id=spec.id,
                initial_grams=Decimal(1000),
                remaining_grams=Decimal(800),
                location_kind=LocationKind.PRINTER,
                printer_id=printer_id,
                ams_unit=0,
                ams_slot=1,
            ),
            MaterialLot(
                spec_id=spec.id,
                initial_grams=Decimal(1000),
                remaining_grams=Decimal(1000),
                location_kind=LocationKind.STOCK,
                shelf="A1",
            ),
        ]
    )
    await db_session.flush()

    stocks = await headroom(db_session)

    assert [stock.code for stock in stocks] == ["PETG-CF-BLACK"]
    assert stocks[0].loaded_grams == Decimal(800)
    assert stocks[0].stock_grams == Decimal(1000)
    assert stocks[0].loaded_printer_ids == [str(printer_id)]


async def test_committed_material_counts_work_that_is_not_sliced_yet(
    db_session: AsyncSession,
) -> None:
    """The material is spoken for the moment the order exists.

    A headroom figure that only counted sliced work would show comfort right up
    until the slicing finished — which is the exact moment it is too late to buy
    more filament.
    """
    order_id = await an_order_id(db_session)
    db_session.add_all(
        [
            a_job(order_id, status=JobStatus.PENDING, grams=Decimal(400)),
            a_job(order_id, status=JobStatus.READY, grams=Decimal(600)),
            # Already off the bed: its material is gone, not committed.
            a_job(order_id, status=JobStatus.SUCCEEDED, grams=Decimal(900)),
        ]
    )
    await db_session.flush()

    committed = {row.material_code: row for row in await committed_material(db_session)}

    assert committed["PETG-CF-BLACK"].grams == Decimal(1000)
    assert committed["PETG-CF-BLACK"].job_count == 2


# ------------------------------------------------------------------ schedule


async def test_the_running_job_shrinks_as_the_machine_works(db_session: AsyncSession) -> None:
    """A bar restating the whole job every refresh would never move."""
    order_id = await an_order_id(db_session)
    printer_id = await a_printer(db_session, name="P-01")
    db_session.add(
        a_job(
            order_id,
            status=JobStatus.PRINTING,
            grams=Decimal(100),
            printer_id=printer_id,
            minutes=Decimal(120),
            progress=75,
        )
    )
    await db_session.flush()

    strip = await schedule(db_session, now=NOW)

    bar = strip.rows[0].bars[0]
    assert bar.ends_at - bar.starts_at == timedelta(minutes=30)
    assert strip.rows[0].free_at == NOW + timedelta(minutes=30)


async def test_queued_work_starts_when_the_job_before_it_ends(db_session: AsyncSession) -> None:
    order_id = await an_order_id(db_session)
    printer_id = await a_printer(db_session, name="P-01")
    db_session.add_all(
        [
            a_job(
                order_id,
                status=JobStatus.PRINTING,
                grams=Decimal(100),
                printer_id=printer_id,
                minutes=Decimal(60),
                progress=0,
            ),
            a_job(
                order_id,
                status=JobStatus.ASSIGNED,
                grams=Decimal(100),
                printer_id=printer_id,
                minutes=Decimal(90),
            ),
        ]
    )
    await db_session.flush()

    strip = await schedule(db_session, now=NOW)

    first, second = strip.rows[0].bars
    assert second.starts_at == first.ends_at
    assert second.ends_at == NOW + timedelta(minutes=150)


# ---------------------------------------------------------------- throughput


async def test_a_farm_that_printed_nothing_has_no_success_rate(
    db_session: AsyncSession,
) -> None:
    """100% for zero prints is the most misleading number a dashboard can carry."""
    measured = await throughput(db_session, since=NOW - timedelta(days=1), until=NOW, machines=4)

    assert measured.success_percent is None
    assert measured.run_hours == Decimal(0)
    assert measured.capacity_hours == Decimal(96)
    assert measured.idle_hours == Decimal(96)


async def test_run_hours_come_from_the_jobs_own_start_and_end(db_session: AsyncSession) -> None:
    order_id = await an_order_id(db_session)
    printer_id = await a_printer(db_session, name="P-01")
    job = a_job(order_id, status=JobStatus.SUCCEEDED, grams=Decimal(50), printer_id=printer_id)
    job.started_at = NOW - timedelta(hours=3)
    job.finished_at = NOW - timedelta(hours=1)
    failed = a_job(order_id, status=JobStatus.FAILED, grams=Decimal(50), printer_id=printer_id)
    failed.started_at = NOW - timedelta(hours=2)
    failed.finished_at = NOW - timedelta(hours=1)
    db_session.add_all([job, failed])
    await db_session.flush()

    measured = await throughput(db_session, since=NOW - timedelta(days=1), until=NOW, machines=1)

    assert measured.run_hours == Decimal(3)
    assert measured.succeeded == 1
    assert measured.failed == 1
    assert measured.success_percent == Decimal("50.0")


# ------------------------------------------------------------- the load map


async def test_a_print_lights_every_hour_it_covered(db_session: AsyncSession) -> None:
    """Not only the hour it finished in.

    An eight-hour print is eight hours of a machine being busy, and a map that
    credited the finishing hour alone would show a farm that works in bursts it
    never worked in.
    """
    order_id = await an_order_id(db_session)
    printer_id = await a_printer(db_session, name="P-01")
    job = a_job(order_id, status=JobStatus.SUCCEEDED, grams=Decimal(10), printer_id=printer_id)
    job.started_at = NOW.replace(hour=9, minute=0)
    job.finished_at = NOW.replace(hour=13, minute=0)
    db_session.add(job)
    await db_session.flush()

    rows = await hourly_load(db_session, now=NOW, machines=1)

    today = rows[-1]
    assert today.weekday == NOW.weekday()
    # 09:00–13:00 is four whole hours on a one-machine farm: full for each.
    assert [today.hours[hour] for hour in (9, 10, 11, 12)] == [Decimal(1)] * 4
    assert today.hours[8] == Decimal(0)
    assert today.hours[13] == Decimal(0)


async def test_load_is_capped_at_the_farms_capacity(db_session: AsyncSession) -> None:
    """Two machines printing through one hour on a two-machine farm is full.

    Without the clamp it reads 100% per machine and sums past it, which turns the
    map's brightest cell into a number that cannot happen.
    """
    order_id = await an_order_id(db_session)
    for index in range(4):
        printer_id = await a_printer(db_session, name=f"P-{index}")
        job = a_job(order_id, status=JobStatus.SUCCEEDED, grams=Decimal(10), printer_id=printer_id)
        job.started_at = NOW.replace(hour=9, minute=0)
        job.finished_at = NOW.replace(hour=10, minute=0)
        db_session.add(job)
    await db_session.flush()

    rows = await hourly_load(db_session, now=NOW, machines=2)

    assert rows[-1].hours[9] == Decimal(1)


async def test_a_farm_with_no_machines_has_no_map(db_session: AsyncSession) -> None:
    """Capacity is the denominator; there is no load to express without one."""
    assert await hourly_load(db_session, now=NOW, machines=0) == []


async def test_a_running_print_counts_as_busy(db_session: AsyncSession) -> None:
    """The map is about occupancy, not about completions.

    A job still on a machine is the clearest case of that machine being busy, and
    counting only finished work would leave the current hour looking idle.
    """
    order_id = await an_order_id(db_session)
    printer_id = await a_printer(db_session, name="P-01")
    job = a_job(order_id, status=JobStatus.PRINTING, grams=Decimal(10), printer_id=printer_id)
    job.started_at = NOW - timedelta(hours=3)
    db_session.add(job)
    await db_session.flush()

    rows = await hourly_load(db_session, now=NOW, machines=1)

    assert rows[-1].hours[(NOW - timedelta(hours=2)).hour] == Decimal(1)
