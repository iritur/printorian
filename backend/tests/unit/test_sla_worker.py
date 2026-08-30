"""The SLA clock.

Phase 2 shipped the promise and the decay policy. What this covers is the thing
that makes them cost something: a pass that recomputes what lateness owes, on
every order still accruing, without a human asking.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.ordering import (
    POLICIES,
    DecayPolicy,
    DraftLine,
    OrderingService,
    OrderStatus,
    PlaceOrder,
)
from printorian.contexts.ordering.events import SlaCreditAccrued
from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    price,
)
from printorian.core.clock import FixedClock
from printorian.core.events import EventBus
from printorian.core.units import Duration, Mass
from printorian.workers.sla import SlaSweep, run_forever

RATES = RateSnapshot()


def a_breakdown():
    return price(
        PriceSpec(
            estimate=PrintEstimate(print_time=Duration.from_hours(3), material_mass=Mass(90)),
            material=MaterialPrice(spec_code="pla-black", price_per_gram=Decimal("2.40")),
        ),
        RATES,
    )


def an_order() -> PlaceOrder:
    return PlaceOrder(
        customer_email="buyer@example.com",
        lines=[
            DraftLine(
                model_name="part.stl",
                material_code="pla-black",
                quantity=1,
                estimated_minutes=Decimal(180),
                estimated_grams=Decimal(90),
            )
        ],
    )


@pytest.fixture
def service(db_session: AsyncSession, clock: FixedClock, bus: EventBus) -> OrderingService:
    return OrderingService(db_session, clock, bus)


async def a_paid_order(service: OrderingService):
    order = await service.place(an_order(), a_breakdown(), RATES)
    order = await service.advance(order.id, OrderStatus.AWAITING_PAYMENT)
    return await service.advance(order.id, OrderStatus.PAID)


async def test_a_late_order_accrues_without_anyone_asking(
    service: OrderingService, clock: FixedClock
) -> None:
    """The whole point: the customer sees the credit while they are still waiting.

    Before the sweep existed, `sla_credit` only moved when an order shipped — so
    the one figure the policy produces was zero for exactly the period it covers.
    """
    order = await a_paid_order(service)
    assert order.sla_credit == 0

    clock.advance(timedelta(days=7, hours=13))  # promised in 5 days
    outcome = await SlaSweep(service).sweep()

    assert outcome.overdue == 1
    assert outcome.accrued == 1
    assert (await service.get(order.id)).sla_credit > 0


async def test_a_second_pass_does_not_charge_the_farm_twice(
    service: OrderingService, clock: FixedClock
) -> None:
    """Recompute, never accumulate — a restart mid-sweep must be harmless."""
    order = await a_paid_order(service)
    clock.advance(timedelta(days=8))

    await SlaSweep(service).sweep()
    once = (await service.get(order.id)).sla_credit
    second = await SlaSweep(service).sweep()

    assert (await service.get(order.id)).sla_credit == once
    # Nothing moved, so nothing is reported as having accrued.
    assert second.accrued == 0
    assert second.overdue == 1


async def test_an_order_inside_its_promise_is_left_alone(
    service: OrderingService, clock: FixedClock
) -> None:
    order = await a_paid_order(service)
    clock.advance(timedelta(days=1))

    outcome = await SlaSweep(service).sweep()

    assert outcome.overdue == 0
    assert (await service.get(order.id)).sla_credit == 0


async def test_a_shipped_order_stops_accruing(service: OrderingService, clock: FixedClock) -> None:
    """The sweep must not thaw a credit that shipping froze.

    A worker that recomputed every late order regardless of status would quietly
    overwrite the figure settled at dispatch, and keep growing it for ever on an
    order already in the customer's hands.
    """
    order = await a_paid_order(service)
    for target in (
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
    assert frozen > 0

    clock.advance(timedelta(days=30))
    outcome = await SlaSweep(service).sweep()

    assert outcome.overdue == 0
    assert (await service.get(order.id)).sla_credit == frozen


async def test_an_unpaid_order_never_accrues(service: OrderingService, clock: FixedClock) -> None:
    """The clock runs from payment. A draft nobody paid for owes nothing."""
    order = await service.place(an_order(), a_breakdown(), RATES)
    clock.advance(timedelta(days=30))

    outcome = await SlaSweep(service).sweep()

    assert outcome.overdue == 0
    assert (await service.get(order.id)).sla_credit == 0


async def test_the_credit_is_announced_so_watching_clients_refresh(
    service: OrderingService, clock: FixedClock, bus: EventBus
) -> None:
    """ADR-0015: the event says *something changed*, and the client re-reads."""
    seen: list[SlaCreditAccrued] = []

    async def remember(event: SlaCreditAccrued) -> None:
        seen.append(event)

    bus.subscribe(SlaCreditAccrued, remember)  # type: ignore[arg-type]

    await a_paid_order(service)
    clock.advance(timedelta(days=8))
    await SlaSweep(service).sweep()

    assert len(seen) == 1
    assert Decimal(seen[0].credit) > 0


async def test_one_broken_order_does_not_stop_the_others(
    service: OrderingService, clock: FixedClock
) -> None:
    """A farm with one unrecomputable order must still pay out on the rest."""
    first = await a_paid_order(service)
    second = await a_paid_order(service)
    clock.advance(timedelta(days=8))

    from printorian.core.errors import NotFoundError

    real = service.refresh_sla_credit
    calls = {"n": 0}

    async def flaky(order_id):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise NotFoundError("error.ordering.not_found", order_id=str(order_id))
        return await real(order_id)

    service.refresh_sla_credit = flaky  # type: ignore[method-assign]
    outcome = await SlaSweep(service).sweep()
    service.refresh_sla_credit = real  # type: ignore[method-assign]

    assert outcome.failed == 1
    assert outcome.accrued == 1
    # Whichever one survived, exactly one of the two was paid out.
    credits = [
        (await service.get(first.id)).sla_credit,
        (await service.get(second.id)).sla_credit,
    ]
    assert sum(1 for credit in credits if credit > 0) == 1


async def test_the_loop_keeps_sweeping_after_a_failed_pass() -> None:
    """One bad pass must not leave every late order accruing nothing for ever."""
    passes = {"n": 0}
    stop = asyncio.Event()

    class Sweep:
        async def sweep(self):  # type: ignore[no-untyped-def]
            passes["n"] += 1
            if passes["n"] == 1:
                raise RuntimeError("database went away")
            stop.set()
            from printorian.workers.sla import SweepOutcome

            return SweepOutcome()

    async def build() -> Sweep:
        return Sweep()

    await asyncio.wait_for(
        run_forever(build, interval_seconds=0, stop=stop),
        timeout=5,
    )
    assert passes["n"] >= 2


async def test_the_loop_stops_promptly_when_asked() -> None:
    """A long interval must not delay shutdown — the stop event wins the race."""
    stop = asyncio.Event()

    class Sweep:
        async def sweep(self):  # type: ignore[no-untyped-def]
            from printorian.workers.sla import SweepOutcome

            stop.set()
            return SweepOutcome()

    async def build() -> Sweep:
        return Sweep()

    # An hour's interval, but the pass sets `stop`, so this returns at once.
    await asyncio.wait_for(run_forever(build, interval_seconds=3600, stop=stop), timeout=5)


async def test_editing_a_policy_does_not_reprice_a_promise_already_sold(
    service: OrderingService, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guarantee `POLICIES` claimed, which the code did not keep.

    The order stored the policy *code* and `_credit_for` re-read the live rates on
    every sweep, so raising `standard` from 5%/day to 10%/day did not price the
    next sale — it re-priced every promise already sold, at the new rate, on the
    next pass. ADR-0020's trap on the other money path.

    The order now carries the three numbers as well as the name, so the edit
    reaches forwards only.
    """
    sold = await a_paid_order(service)
    # Promised in five days, so eight days on is two and a half past the grace.
    clock.advance(timedelta(days=8))
    await SlaSweep(service).sweep()

    before = (await service.get(sold.id)).sla_credit
    assert before == (sold.total * Decimal("12.5") / 100).quantize(Decimal("0.01"))

    monkeypatch.setitem(POLICIES, "standard", DecayPolicy(percent_per_day=Decimal(10)))
    later = await a_paid_order(service)

    # The sweep that used to double this order's credit without anything about the
    # order having changed.
    await SlaSweep(service).sweep()
    assert (await service.get(sold.id)).sla_credit == before

    # The edit is not ignored — it applies to what was sold after it. `later` sits
    # exactly where `sold` did a moment ago, two and a half days past its grace,
    # and owes twice as much for it.
    clock.advance(timedelta(days=8))
    await SlaSweep(service).sweep()
    assert (await service.get(later.id)).sla_credit == (later.total * Decimal(25) / 100).quantize(
        Decimal("0.01")
    )
