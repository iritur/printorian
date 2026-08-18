"""Order lifecycle and the SLA decay policy.

Two named rules live here because they are the ones people will argue about later:
which state an order may move to, and what a late delivery costs the farm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from printorian.core.errors import DomainRuleViolationError


class DeliveryMethod(StrEnum):
    """How the finished parts reach the customer.

    A pricing input, not a label. Collection has no shipping line at all — not a
    zero one — because the engine omits what does not apply rather than printing a
    nought the customer has to interpret.
    """

    PICKUP = "pickup"
    COURIER = "courier"
    FREIGHT = "freight"

    @property
    def is_shipped(self) -> bool:
        """Whether the farm has to get this order to an address."""
        return self is not DeliveryMethod.PICKUP


class OrderStatus(StrEnum):
    """Where an order is in its life.

    Mirrors the scenario's customer-visible workflow, with two states that exist
    because reality demanded them:

    * ``PREP`` — a human has to slice this before anything can be dispatched
      (ADR-0006). Skipped when a matching prepared plate is already cached.
    * ``PRICE_REVIEW`` — the sliced truth exceeded the quote beyond tolerance, so
      the order is held for a decision rather than silently costing the farm
      money or silently overcharging the customer (ADR-0013).
    """

    DRAFT = "draft"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    PREP = "prep"
    PRICE_REVIEW = "price_review"
    QUEUED = "queued"
    PRINTING = "printing"
    POST_PRODUCTION = "post_production"
    QUALITY_CHECK = "quality_check"
    PACKING = "packing"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

    @property
    def is_terminal(self) -> bool:
        return self in {OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.REFUNDED}

    @property
    def is_paid(self) -> bool:
        """Money has been taken. Cancelling from here owes the customer a refund."""
        return self not in {
            OrderStatus.DRAFT,
            OrderStatus.AWAITING_PAYMENT,
            OrderStatus.CANCELLED,
        }

    @property
    def counts_against_sla(self) -> bool:
        """The clock runs from payment until the parcel leaves."""
        return self.is_paid and self not in {
            OrderStatus.SHIPPED,
            OrderStatus.COMPLETED,
            OrderStatus.REFUNDED,
        }


#: The only transitions the system will perform. Anything else is a bug, not a
#: business decision, and is refused loudly rather than half-applied.
TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED}),
    OrderStatus.AWAITING_PAYMENT: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.PREP, OrderStatus.QUEUED, OrderStatus.REFUNDED}),
    OrderStatus.PREP: frozenset(
        {OrderStatus.QUEUED, OrderStatus.PRICE_REVIEW, OrderStatus.REFUNDED}
    ),
    OrderStatus.PRICE_REVIEW: frozenset({OrderStatus.QUEUED, OrderStatus.REFUNDED}),
    OrderStatus.QUEUED: frozenset({OrderStatus.PRINTING, OrderStatus.REFUNDED}),
    # Printing can fail and go round again — a remake re-enters the queue.
    OrderStatus.PRINTING: frozenset(
        {OrderStatus.POST_PRODUCTION, OrderStatus.QUEUED, OrderStatus.REFUNDED}
    ),
    OrderStatus.POST_PRODUCTION: frozenset({OrderStatus.QUALITY_CHECK, OrderStatus.REFUNDED}),
    # QC failure sends the work back, either to finishing or to be reprinted.
    OrderStatus.QUALITY_CHECK: frozenset(
        {OrderStatus.PACKING, OrderStatus.POST_PRODUCTION, OrderStatus.QUEUED}
    ),
    OrderStatus.PACKING: frozenset({OrderStatus.SHIPPED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.COMPLETED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REFUNDED: frozenset(),
}


#: Orders still being worked on. Named here rather than spelled out at each call
#: site because it is also a *database* fact: it is the predicate of the partial
#: index the order desk reads through, and an index predicate that disagreed with
#: the application's idea of "open" would silently stop matching.
OPEN_STATUSES: frozenset[OrderStatus] = frozenset(
    status for status in OrderStatus if not status.is_terminal
)


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: OrderStatus, target: OrderStatus) -> None:
    if not can_transition(current, target):
        raise DomainRuleViolationError(
            "error.ordering.invalid_transition",
            current=current.value,
            target=target.value,
            allowed=sorted(state.value for state in TRANSITIONS[current]),
        )


# --------------------------------------------------------------------- SLA


@dataclass(frozen=True, slots=True, kw_only=True)
class DecayPolicy:
    """What being late costs, as a rule rather than a case-by-case apology.

    The scenario asks that a customer kept waiting gets a cheaper model. Expressing
    that as a named, capped policy means the discount is predictable, auditable, and
    cannot quietly grow without bound on one badly stuck order.
    """

    code: str = "standard"
    #: Percent of the order total forgiven per full day late.
    percent_per_day: Decimal = Decimal(5)
    #: Nothing accrues inside this window — a few hours late is not a discount.
    grace: timedelta = timedelta(hours=12)
    #: Hard ceiling. Without one, a stalled order eventually costs more than it earns.
    max_percent: Decimal = Decimal(30)

    def __post_init__(self) -> None:
        if self.percent_per_day < 0:
            raise DomainRuleViolationError("error.ordering.decay_negative")
        if not (Decimal(0) <= self.max_percent <= Decimal(100)):
            raise DomainRuleViolationError("error.ordering.decay_cap")

    def percent_at(self, *, promised_at: datetime, now: datetime) -> Decimal:
        """Credit percent owed at ``now`` for a delivery promised at ``promised_at``."""
        late = now - promised_at - self.grace
        if late <= timedelta(0):
            return Decimal(0)

        days = Decimal(late.total_seconds()) / Decimal(86400)
        return min(self.max_percent, (days * self.percent_per_day).quantize(Decimal("0.01")))


#: Named policies, so an order records *which* rule it was sold under and a later
#: change to the standard terms cannot retroactively alter an existing promise.
POLICIES: dict[str, DecayPolicy] = {
    "standard": DecayPolicy(),
    "none": DecayPolicy(code="none", percent_per_day=Decimal(0), max_percent=Decimal(0)),
    "strict": DecayPolicy(
        code="strict",
        percent_per_day=Decimal(10),
        grace=timedelta(hours=2),
        max_percent=Decimal(50),
    ),
}


def policy(code: str) -> DecayPolicy:
    found = POLICIES.get(code)
    if found is None:
        raise DomainRuleViolationError("error.ordering.unknown_decay_policy", policy=code)
    return found
