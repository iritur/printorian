"""The rates behind a quote are stored, not merely hashed.

ADR-0002 promises an old quote can be recomputed years later. The order has always
carried the snapshot *hash* — which proves which rates were used and detects
tampering — but the values behind it lived only in code, so changing a rate made
every older hash unresolvable. These are the tests that the hash now resolves.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import OrderingService
from printorian.contexts.ordering.models import RateSnapshotRecord
from printorian.contexts.ordering.schemas import DraftLine, PlaceOrder
from printorian.contexts.pricing import (
    DiscountLadder,
    DiscountTier,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
    rates_from_dict,
    rates_to_dict,
)
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.units import Duration, Mass


def a_spec() -> PriceSpec:
    return PriceSpec(
        estimate=PrintEstimate(print_time=Duration.from_hours(3), material_mass=Mass(90)),
        material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
    )


def an_order() -> PlaceOrder:
    return PlaceOrder(
        customer_email="buyer@example.com",
        lines=[
            DraftLine(
                model_name="part.stl",
                material_code="pla-black",
                estimated_minutes=Decimal(180),
                estimated_grams=Decimal(90),
            )
        ],
    )


@pytest.fixture
def service(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> OrderingService:
    return OrderingService(db_session, clock, bus)


# ------------------------------------------------------ serialization


def test_rates_round_trip_to_the_same_snapshot() -> None:
    """Every rate survives the round trip — proven by the hash, not by eyeballing.

    The id is a digest over every field, so an identical `snapshot_id` after a
    round trip means nothing was dropped, coerced or reordered. This is the test
    that catches a newly-added rate whose type the serializer cannot carry.
    """
    rates = RateSnapshot(
        margin_percent=Decimal("33.5"),
        guard_tier_cliffs=False,
        discounts=DiscountLadder(
            tiers=(
                DiscountTier(min_quantity=10, percent=Decimal(5)),
                DiscountTier(min_quantity=50, percent=Decimal("12.5")),
            )
        ),
    )

    restored = rates_from_dict(rates_to_dict(rates))

    assert restored == rates
    assert restored.snapshot_id == rates.snapshot_id


def test_the_stored_payload_carries_its_own_id() -> None:
    """A row can be checked against its key without rebuilding the object."""
    rates = RateSnapshot()
    assert rates_to_dict(rates)["snapshot_id"] == rates.snapshot_id


def test_every_rate_field_is_serialized() -> None:
    """A field added to `RateSnapshot` and forgotten here would fail loudly.

    Belt and braces alongside the hash check: this one names the missing field
    instead of only reporting that two digests differ.
    """
    from dataclasses import fields

    payload = rates_to_dict(RateSnapshot())
    for field in fields(RateSnapshot):
        assert field.name in payload, f"rate {field.name!r} is not serialized"


# -------------------------------------------------------- persistence


async def test_placing_an_order_stores_the_rates_it_was_priced_with(
    service: OrderingService, db_session: AsyncSession
) -> None:
    """The hash on the order resolves to a row, so the quote can be rebuilt."""
    rates = RateSnapshot()
    order = await service.place(an_order(), price(a_spec(), rates), rates)

    stored = await db_session.get(RateSnapshotRecord, order.rate_snapshot_id)
    assert stored is not None
    assert order.rate_snapshot_id == rates.snapshot_id
    assert rates_from_dict(stored.payload) == rates


async def test_the_stored_rates_reprice_the_order_identically(
    service: OrderingService, db_session: AsyncSession
) -> None:
    """The point of the whole exercise: the engine can be re-run on stored rates.

    This is what "reproducible" means and what the hash alone could never deliver —
    it could confirm the rates were the same, never say what they were.
    """
    rates = RateSnapshot(margin_percent=Decimal("41"))
    original = price(a_spec(), rates)
    order = await service.place(an_order(), original, rates)

    stored = await db_session.get(RateSnapshotRecord, order.rate_snapshot_id)
    assert stored is not None
    recomputed = price(a_spec(), rates_from_dict(stored.payload))

    assert recomputed.total == original.total
    assert recomputed.rate_snapshot_id == original.rate_snapshot_id


async def test_identical_rates_are_stored_once(
    service: OrderingService, db_session: AsyncSession
) -> None:
    """The key is the content hash, so two orders priced alike share one row.

    A second row would mean the id was not really content-addressed, and the
    "identical rates always produce the same id" claim in `rates.py` would be
    describing something the storage layer quietly contradicted.
    """
    rates = RateSnapshot()
    await service.place(an_order(), price(a_spec(), rates), rates)
    await service.place(an_order(), price(a_spec(), rates), rates)

    rows = list(await db_session.scalars(select(RateSnapshotRecord)))
    assert len(rows) == 1


async def test_different_rates_are_stored_separately(
    service: OrderingService, db_session: AsyncSession
) -> None:
    """Two price books, two rows — and each order points at its own."""
    cheap = RateSnapshot(margin_percent=Decimal(20))
    dear = RateSnapshot(margin_percent=Decimal(60))

    first = await service.place(an_order(), price(a_spec(), cheap), cheap)
    second = await service.place(an_order(), price(a_spec(), dear), dear)

    assert first.rate_snapshot_id != second.rate_snapshot_id
    rows = list(await db_session.scalars(select(RateSnapshotRecord)))
    assert len(rows) == 2
