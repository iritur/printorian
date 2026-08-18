"""Ordering: pinned prices, the state machine, and the SLA clock."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import (
    DraftLine,
    OrderingService,
    OrderStatus,
    PlaceOrder,
    can_transition,
    policy,
)
from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
)
from printorian.contexts.pricing import breakdown_from_dict as from_dict
from printorian.contexts.pricing import breakdown_to_dict as to_dict
from printorian.core.clock import FixedClock
from printorian.core.errors import DomainRuleViolationError
from printorian.core.events import EventBus
from printorian.core.units import Duration, Mass

RATES = RateSnapshot()


def a_breakdown(quantity: int = 1):
    return price(
        PriceSpec(
            estimate=PrintEstimate(print_time=Duration.from_hours(3), material_mass=Mass(90)),
            material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
            quantity=quantity,
        ),
        RATES,
    )


def an_order(lines: int = 1) -> PlaceOrder:
    return PlaceOrder(
        customer_email="Buyer@Example.com",
        lines=[
            DraftLine(
                model_name=f"part-{index}.stl",
                material_code="pla-black",
                quantity=1,
                estimated_minutes=Decimal(180),
                estimated_grams=Decimal(90),
            )
            for index in range(lines)
        ],
    )


@pytest.fixture
def service(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> OrderingService:
    return OrderingService(db_session, clock, bus)


# ------------------------------------------------------------ placing


async def test_placing_pins_the_price_verbatim(service: OrderingService) -> None:
    """The agreed figures are stored, not a recipe for recomputing them."""
    breakdown = a_breakdown()
    order = await service.place(an_order(), breakdown, RATES)

    assert order.total == breakdown.total.amount
    assert order.rate_snapshot_id == breakdown.rate_snapshot_id
    assert order.engine_version == breakdown.engine_version

    restored = from_dict(order.price_breakdown)
    assert restored.total == breakdown.total
    assert [line.code for line in restored.lines] == [line.code for line in breakdown.lines]


async def test_pinned_price_survives_a_rate_change(service: OrderingService) -> None:
    """A later change to the farm's rates must not alter an existing order."""
    order = await service.place(an_order(), a_breakdown(), RATES)
    original = order.total

    dearer = RateSnapshot(margin_percent=Decimal(60))
    assert (
        price(
            PriceSpec(
                estimate=PrintEstimate(print_time=Duration.from_hours(3), material_mass=Mass(90)),
                material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
            ),
            dearer,
        ).total.amount
        > original
    )

    assert (await service.get(order.id)).total == original


async def test_order_numbers_are_sequential_and_unique(service: OrderingService) -> None:
    first = await service.place(an_order(), a_breakdown(), RATES)
    second = await service.place(an_order(), a_breakdown(), RATES)

    assert first.number == "PR-000001"
    assert second.number == "PR-000002"


async def test_email_is_normalized(service: OrderingService) -> None:
    order = await service.place(an_order(), a_breakdown(), RATES)
    assert order.customer_email == "buyer@example.com"


async def test_line_total_matches_the_order_total(service: OrderingService) -> None:
    """Apportioned from the order total, never priced a second time."""
    breakdown = a_breakdown(quantity=3)
    order = await service.place(an_order(), breakdown, RATES)

    assert sum(line.line_total for line in order.lines) == order.total


def test_multi_line_orders_are_refused_for_now() -> None:
    """A cart needs a defined rule for combining separately-priced breakdowns.

    Until that rule exists, accepting several lines would mean inventing one
    quietly — which is how a second pricing path is born (ADR-0002).
    """
    with pytest.raises(ValueError):
        an_order(lines=3)


async def test_placing_records_an_event_and_publishes_one(
    service: OrderingService, bus: EventBus
) -> None:
    async with bus.collecting() as published:
        order = await service.place(an_order(), a_breakdown(), RATES)

    assert [event.name for event in published] == ["order.placed"]
    assert order.events[0].reason == "order.placed"


# ------------------------------------------------------ state machine


async def test_the_happy_path_runs_end_to_end(service: OrderingService) -> None:
    order = await service.place(an_order(), a_breakdown(), RATES)
    for target in (
        OrderStatus.AWAITING_PAYMENT,
        OrderStatus.PAID,
        OrderStatus.PREP,
        OrderStatus.QUEUED,
        OrderStatus.PRINTING,
        OrderStatus.POST_PRODUCTION,
        OrderStatus.QUALITY_CHECK,
        OrderStatus.PACKING,
        OrderStatus.SHIPPED,
        OrderStatus.COMPLETED,
    ):
        order = await service.advance(order.id, target)
    assert order.status is OrderStatus.COMPLETED


async def test_an_illegal_transition_is_refused_with_the_allowed_set(
    service: OrderingService,
) -> None:
    order = await service.place(an_order(), a_breakdown(), RATES)

    with pytest.raises(DomainRuleViolationError) as excinfo:
        await service.advance(order.id, OrderStatus.SHIPPED)

    assert excinfo.value.code == "error.ordering.invalid_transition"
    assert "awaiting_payment" in excinfo.value.details["allowed"]


async def test_paying_and_shipping_are_timestamped(
    service: OrderingService, clock: FixedClock
) -> None:
    order = await service.place(an_order(), a_breakdown(), RATES)
    order = await service.advance(order.id, OrderStatus.AWAITING_PAYMENT)
    order = await service.advance(order.id, OrderStatus.PAID)

    assert order.paid_at is not None
    assert order.shipped_at is None


async def test_qc_failure_can_send_work_back(service: OrderingService) -> None:
    """Rework is a real path, not an exception to be handled by hand."""
    assert can_transition(OrderStatus.QUALITY_CHECK, OrderStatus.POST_PRODUCTION)
    assert can_transition(OrderStatus.QUALITY_CHECK, OrderStatus.QUEUED)
    assert can_transition(OrderStatus.PRINTING, OrderStatus.QUEUED)


async def test_terminal_states_go_nowhere() -> None:
    for status in (OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        assert status.is_terminal
        assert not can_transition(status, OrderStatus.QUEUED)


async def test_every_transition_is_recorded_with_its_predecessor(
    service: OrderingService,
) -> None:
    order = await service.place(an_order(), a_breakdown(), RATES)
    order = await service.advance(order.id, OrderStatus.AWAITING_PAYMENT, reason="checkout")

    last = order.events[-1]
    assert last.from_status is OrderStatus.DRAFT
    assert last.to_status is OrderStatus.AWAITING_PAYMENT
    assert last.reason == "checkout"


# ---------------------------------------------------------------- SLA


def test_no_credit_before_the_promise_or_inside_the_grace_window() -> None:
    standard = policy("standard")
    from datetime import UTC, datetime

    promised = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

    assert standard.percent_at(promised_at=promised, now=promised) == 0
    assert standard.percent_at(promised_at=promised, now=promised + timedelta(hours=11)) == 0


def test_credit_accrues_per_day_and_is_capped() -> None:
    standard = policy("standard")
    from datetime import UTC, datetime

    promised = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

    two_days = standard.percent_at(promised_at=promised, now=promised + timedelta(days=2, hours=12))
    assert two_days == Decimal(10)

    # Without a cap a stuck order would eventually cost more than it earned.
    forever = standard.percent_at(promised_at=promised, now=promised + timedelta(days=400))
    assert forever == standard.max_percent


async def test_late_order_accrues_a_credit_the_customer_can_see(
    service: OrderingService, clock: FixedClock
) -> None:
    order = await service.place(an_order(), a_breakdown(), RATES)
    order = await service.advance(order.id, OrderStatus.AWAITING_PAYMENT)
    order = await service.advance(order.id, OrderStatus.PAID)

    clock.advance(timedelta(days=7, hours=13))  # promised in 5 days
    order = await service.refresh_sla_credit(order.id)

    assert order.sla_credit > 0
    assert order.payable_now < order.total


async def test_credit_is_recomputed_not_accumulated(
    service: OrderingService, clock: FixedClock
) -> None:
    """Running the worker twice must not charge the farm twice."""
    order = await service.place(an_order(), a_breakdown(), RATES)
    clock.advance(timedelta(days=8))

    once = (await service.refresh_sla_credit(order.id)).sla_credit
    twice = (await service.refresh_sla_credit(order.id)).sla_credit
    assert once == twice


async def test_shipping_freezes_the_credit(service: OrderingService, clock: FixedClock) -> None:
    """The clock stops when the parcel leaves, not when someone clicks 'completed'."""
    order = await service.place(an_order(), a_breakdown(), RATES)
    for target in (
        OrderStatus.AWAITING_PAYMENT,
        OrderStatus.PAID,
        OrderStatus.QUEUED,
        OrderStatus.PRINTING,
        OrderStatus.POST_PRODUCTION,
        OrderStatus.QUALITY_CHECK,
        OrderStatus.PACKING,
    ):
        order = await service.advance(order.id, target)

    clock.advance(timedelta(days=7))
    order = await service.advance(order.id, OrderStatus.SHIPPED)
    frozen = order.sla_credit

    clock.advance(timedelta(days=30))
    assert (await service.refresh_sla_credit(order.id)).sla_credit == frozen


async def test_the_none_policy_never_charges_the_farm() -> None:
    from datetime import UTC, datetime

    promised = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assert policy("none").percent_at(promised_at=promised, now=promised + timedelta(days=99)) == 0


def test_an_unknown_policy_is_refused() -> None:
    with pytest.raises(DomainRuleViolationError):
        policy("generous")


async def test_overdue_lists_only_orders_still_owed_work(
    service: OrderingService, clock: FixedClock
) -> None:
    late = await service.place(an_order(), a_breakdown(), RATES)
    late = await service.advance(late.id, OrderStatus.AWAITING_PAYMENT)
    late = await service.advance(late.id, OrderStatus.PAID)

    clock.advance(timedelta(days=10))
    overdue = await service.overdue()
    assert [order.number for order in overdue] == [late.number]


# ------------------------------------------------------------- table


async def test_table_reports_rows_and_a_count_for_every_status(
    service: OrderingService,
) -> None:
    await service.place(an_order(), a_breakdown(), RATES)
    table = await service.table()

    assert table.total == 1
    assert {entry.status for entry in table.counts} == set(OrderStatus)
    assert next(e.count for e in table.counts if e.status is OrderStatus.DRAFT) == 1


async def test_breakdown_round_trips_through_storage() -> None:
    breakdown = a_breakdown(quantity=4)
    restored = from_dict(to_dict(breakdown))

    assert restored.total == breakdown.total
    assert restored.by_category() == breakdown.by_category()
    for original, copy in zip(breakdown.lines, restored.lines, strict=True):
        assert copy.amount == original.amount
        assert copy.basis.kind is original.basis.kind
        assert copy.basis.of_codes == original.basis.of_codes


async def test_an_order_reports_where_it_may_go_next(service: OrderingService) -> None:
    """The order desk renders its buttons from this, so it cannot offer a move the
    API would then refuse. The alternative is the transition table in two languages."""
    order = await service.place(an_order(), a_breakdown(), RATES)

    assert order.status is OrderStatus.DRAFT
    assert set(order.allowed_transitions) == {
        OrderStatus.AWAITING_PAYMENT,
        OrderStatus.CANCELLED,
    }


async def test_a_terminal_order_offers_nothing(service: OrderingService) -> None:
    order = await service.place(an_order(), a_breakdown(), RATES)
    cancelled = await service.advance(order.id, OrderStatus.CANCELLED, reason="test")

    assert cancelled.allowed_transitions == []
