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

from printorian.contexts.ordering import OrderingService, rate_snapshot_for
from printorian.contexts.ordering.models import Order, RateSnapshotRecord
from printorian.contexts.ordering.schemas import DraftLine, PlaceOrder
from printorian.contexts.pricing import (
    LOGISTICS_SHIPPING,
    DiscountLadder,
    DiscountTier,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    ShippingZone,
    ZoneTariffs,
    price,
    rates_from_dict,
    rates_to_dict,
)
from printorian.core.clock import FixedClock
from printorian.core.errors import NotFoundError
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


# ------------------------------------------------------ the zone tariff


def zoned_rates(per_kg: str = "60") -> RateSnapshot:
    return RateSnapshot(
        zones=ZoneTariffs(
            zones=(
                ShippingZone(
                    code="msk", base=Decimal(400), transit_days=1, postcode_prefixes=("101", "1")
                ),
                ShippingZone(
                    code="cfo",
                    base=Decimal("550.50"),
                    per_kg=Decimal(per_kg),
                    transit_days=3,
                    postcode_prefixes=("3",),
                    enabled=False,
                ),
            )
        )
    )


def test_the_whole_zone_table_survives_the_round_trip() -> None:
    """ADR-0020's mechanism: the tariff is archived with the order, not referenced.

    A farm that re-draws its zones next month must not be able to move the price
    of an order it already sold, and this is what makes that true — the table is
    in the payload rather than being looked up again at read time.
    """
    rates = zoned_rates()
    restored = rates_from_dict(rates_to_dict(rates))
    assert restored == rates
    assert restored.snapshot_id == rates.snapshot_id


def test_zone_money_is_stored_as_strings_not_json_numbers() -> None:
    """A JSON number is a float, and a float is not a price."""
    stored = rates_to_dict(zoned_rates())["zones"]
    assert stored[1]["base"] == "550.50"
    assert stored[1]["per_kg"] == "60"
    assert stored[1]["enabled"] is False
    assert stored[0]["postcode_prefixes"] == ["101", "1"]


def test_a_kopeck_on_one_zone_changes_the_snapshot_id() -> None:
    """Without this the pinned snapshot could be re-used for a re-drawn tariff.

    That is ADR-0020 failing *silently*: the id is the primary key of the archive,
    so two different tariffs sharing one id means an order is stored against rates
    it was never priced at.
    """
    assert zoned_rates().snapshot_id == zoned_rates().snapshot_id
    assert zoned_rates().snapshot_id != zoned_rates(per_kg="60.01").snapshot_id


def test_a_snapshot_stored_before_zones_existed_still_reprices() -> None:
    """Hand-written rather than generated, because that is the real archive.

    Every row already in `rate_snapshots` was written without a `zones` key.
    `rates_from_dict` skips absent fields, so such a row rebuilds with an empty
    table and reproduces the flat figure it was sold at — verified here rather
    than assumed.
    """
    payload = rates_to_dict(RateSnapshot())
    del payload["zones"]

    restored = rates_from_dict(payload)

    assert restored.zones == ZoneTariffs()
    breakdown = price(a_spec(), restored)
    assert breakdown.amount_of(LOGISTICS_SHIPPING).amount == RateSnapshot().shipping_flat


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


# ------------------------------------------------------ reading them back


async def test_the_stored_rates_are_served_as_stored_rather_than_rebuilt(
    service: OrderingService, db_session: AsyncSession
) -> None:
    """The payload comes back as it was written, not through `rates_from_dict`.

    That function fills any field absent from a stored row with today's default,
    which is exactly the wrong behaviour here: a snapshot written before a rate
    existed would come back carrying a number that was never in force, and it
    would be indistinguishable from a measured one (ADR-0007). The row is served
    verbatim, `schema_version` and all, so its vintage is legible.
    """
    rates = RateSnapshot(margin_percent=Decimal(20))
    order = await service.place(an_order(), price(a_spec(), rates), rates)

    view = await rate_snapshot_for(db_session, order.id)

    assert view.id == order.rate_snapshot_id
    assert view.payload == rates_to_dict(rates)
    assert view.payload["margin_percent"] == "20"


async def test_an_order_with_no_snapshot_says_so_rather_than_inventing_rates(
    service: OrderingService, db_session: AsyncSession
) -> None:
    """An order placed before ADR-0020 pinned nothing, and must say nothing.

    `rate_snapshot_id` is nullable precisely so it can carry that fact. The code
    is deliberately distinct from `not_found`: the order exists, and the honest
    answer is that its rates were never recorded — a screen renders that, where a
    table of zeros would be a claim about rates nobody ever charged.
    """
    order = await service.place(an_order(), price(a_spec(), RateSnapshot()), RateSnapshot())
    row = await db_session.get(Order, order.id)
    assert row is not None
    row.rate_snapshot_id = None
    await db_session.flush()

    with pytest.raises(NotFoundError) as raised:
        await rate_snapshot_for(db_session, order.id)

    assert raised.value.code == "error.ordering.rates_not_recorded"
