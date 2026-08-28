"""Whether the farm can tell that a listing it never paged has outgrown that choice.

`DATABASE-REVIEW` §9 records `fleet.table()` and the materials listing as
deliberately unpaged, and the argument is sound: both are bounded by the size of
the farm rather than by history. What it left as a note was the expiry date. The
trigger in [#45](https://github.com/iritur/printorian/issues/45) is "either listing
exceeds a few hundred rows", and the case it names as the dangerous one is the
purchasing screen putting three more purchasable classes into the materials
listing — growth that arrives as a *feature*, on a day when nobody is looking at
row counts.

Three things can silently break, so each is driven separately:

* the verdict, which is a pure comparison and is tested as one;
* the honesty of a saturated reading, which is the part root CLAUDE.md §1 is about
  — a capped count reported as an exact one is a number the farm did not measure;
* the counts themselves, against real rows, because a predicate that had drifted
  from the listing it claims to measure would leave a check reporting "fine" for
  ever.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import listings as fleet_listings
from printorian.contexts.fleet.models import Printer
from printorian.contexts.inventory import listings as inventory_listings
from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.core import pagination
from printorian.core.pagination import UNPAGINATED_ROW_TRIGGER, ListingSize, capped_size

# ------------------------------------------------------------ the verdict


def test_a_small_listing_is_not_past_the_trigger() -> None:
    assert ListingSize(listing="printers", rows=6, is_exact=True).past_trigger is False


def test_the_boundary_is_inclusive() -> None:
    """An approximate trigger for work that takes days to schedule, so the argument
    worth not having is the one about row 500."""
    under = ListingSize(listing="x", rows=UNPAGINATED_ROW_TRIGGER - 1, is_exact=True)
    at = ListingSize(listing="x", rows=UNPAGINATED_ROW_TRIGGER, is_exact=False)
    assert under.past_trigger is False
    assert at.past_trigger is True


# ------------------------------------------------------------ the honesty of a reading


def test_a_count_below_the_cap_is_an_exact_figure() -> None:
    assert capped_size("printers", 6) == ListingSize(listing="printers", rows=6, is_exact=True)


def test_a_count_that_reached_the_cap_is_a_floor_and_says_so() -> None:
    """The reading stops counting at the trigger, so the figure beside the verdict
    is "at least this many" rather than "this many". Reporting it as exact would be
    a number nothing measured (root CLAUDE.md §1) — and it is the only part of this
    module that can lie, because the verdict is right either way."""
    size = capped_size("materials", UNPAGINATED_ROW_TRIGGER)
    assert size.is_exact is False
    assert size.past_trigger is True


def test_one_saturated_part_makes_the_whole_reading_a_floor() -> None:
    """The materials reading is two counts summed. A part that hit its own cap
    makes the total a floor even though the other part is exact."""
    size = capped_size("materials", 12, UNPAGINATED_ROW_TRIGGER)
    assert size.is_exact is False
    assert size.rows == UNPAGINATED_ROW_TRIGGER + 12


def test_parts_that_are_each_small_can_still_add_up_past_the_trigger() -> None:
    """Which is why the materials listing is counted on both axes at once: a
    catalogue of 300 specs carrying 300 live lots serialises 600 objects, and
    paging the specs alone would not have bounded that response."""
    half = UNPAGINATED_ROW_TRIGGER // 2 + 1
    size = capped_size("materials", half, half)
    assert size.is_exact is True
    assert size.past_trigger is True


# ------------------------------------------------------------ against real rows


async def test_the_printers_reading_counts_what_the_listing_returns(
    db_session: AsyncSession,
) -> None:
    """Including the retired ones, which is the half that only ever climbs.

    `GET /printers?include_inactive=true` returns them, nothing ever deletes a
    printer row, and a reading that filtered them out would be watching the number
    bounded by the size of the farm instead of the number that is not.
    """
    assert (await fleet_listings.printer_listing_size(db_session)).rows == 0

    db_session.add(Printer(name="A"))
    db_session.add(Printer(name="Retired", is_active=False))
    await db_session.commit()

    size = await fleet_listings.printer_listing_size(db_session)
    assert size == ListingSize(listing="printers", rows=2, is_exact=True)


async def test_the_materials_reading_counts_specs_and_the_lots_nested_in_them(
    db_session: AsyncSession,
) -> None:
    """One spec plus its two live spools is three objects in the response, not one."""
    spec = MaterialSpec(code="PLA-1", name="PLA", family="PLA", sell_price_per_gram=Decimal("2.5"))
    db_session.add(spec)
    await db_session.flush()
    for _ in range(2):
        db_session.add(
            MaterialLot(spec_id=spec.id, initial_grams=Decimal(1000), remaining_grams=Decimal(1000))
        )
    await db_session.commit()

    size = await inventory_listings.material_listing_size(db_session)
    assert size == ListingSize(listing="materials", rows=3, is_exact=True)


async def test_the_materials_reading_leaves_out_what_the_listing_leaves_out(
    db_session: AsyncSession,
) -> None:
    """An inactive spec is filtered out of `InventoryService.table()`
    unconditionally — there is no `include_inactive` on `/materials` — and an empty
    spool is dropped by `_to_view`. Counting either would measure a response the
    endpoint cannot produce, and the check would then fire over rows nobody is
    being sent."""
    active = MaterialSpec(
        code="PLA-1", name="PLA", family="PLA", sell_price_per_gram=Decimal("2.5")
    )
    retired = MaterialSpec(
        code="ABS-1", name="ABS", family="ABS", sell_price_per_gram=Decimal(3), is_active=False
    )
    db_session.add_all([active, retired])
    await db_session.flush()
    db_session.add(
        MaterialLot(spec_id=active.id, initial_grams=Decimal(1000), remaining_grams=Decimal(0))
    )
    db_session.add(
        MaterialLot(spec_id=retired.id, initial_grams=Decimal(1000), remaining_grams=Decimal(500))
    )
    await db_session.commit()

    size = await inventory_listings.material_listing_size(db_session)
    assert size == ListingSize(listing="materials", rows=1, is_exact=True)


async def test_the_count_stops_at_the_trigger_rather_than_reading_the_table(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property that makes this safe on the readiness path.

    With the trigger at two, four printers still read as two — the query stopped —
    and the reading calls itself inexact rather than reporting a figure it did not
    finish taking. A plain `count(*)` would answer four, and would go on answering
    truthfully right up to the point where the probe became the outage.
    """
    monkeypatch.setattr(pagination, "UNPAGINATED_ROW_TRIGGER", 2)
    db_session.add_all([Printer(name=f"P{index}") for index in range(4)])
    await db_session.commit()

    size = await fleet_listings.printer_listing_size(db_session)
    assert size.rows == 2
    assert size.is_exact is False
    assert size.past_trigger is True


async def test_the_test_farm_is_nowhere_near_either_trigger(db_session: AsyncSession) -> None:
    """Both whole checks, end to end, on an empty farm."""
    assert await fleet_listings.printers_listing_oversized(db_session) is False
    assert await inventory_listings.materials_listing_oversized(db_session) is False
