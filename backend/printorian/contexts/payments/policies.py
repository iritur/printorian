"""Payment states and the rules that protect the money.

Three rules here exist because payment integrations fail in the same three ways
everywhere, and none of them are theoretical:

1. **Providers deliver a webhook more than once.** Retries, at-least-once delivery,
   an operator replaying a failed notification. Every handler must be idempotent, so
   a second delivery of the same event changes nothing.
2. **The amount must come from the order, never from the request.** A client that
   can name its own price will eventually name a lower one.
3. **What the provider says it captured must match what was owed.** A mismatch is
   held for a human rather than silently accepted in either direction.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from printorian.core.errors import DomainRuleViolationError


class PaymentStatus(StrEnum):
    """Where one payment attempt stands."""

    #: Created locally; the customer has not been sent to the provider yet.
    CREATED = "created"
    #: Awaiting the customer at the provider, or awaiting an operator for manual.
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_settled(self) -> bool:
        """Money actually moved and has not been fully returned."""
        return self in {PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED}

    @property
    def is_final(self) -> bool:
        return self in {
            PaymentStatus.REFUNDED,
            PaymentStatus.CANCELLED,
            PaymentStatus.FAILED,
        }


TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.CREATED: frozenset(
        {PaymentStatus.PENDING, PaymentStatus.CANCELLED, PaymentStatus.FAILED}
    ),
    PaymentStatus.PENDING: frozenset(
        {PaymentStatus.SUCCEEDED, PaymentStatus.CANCELLED, PaymentStatus.FAILED}
    ),
    PaymentStatus.SUCCEEDED: frozenset({PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED}),
    PaymentStatus.PARTIALLY_REFUNDED: frozenset(
        {PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED}
    ),
    PaymentStatus.REFUNDED: frozenset(),
    PaymentStatus.CANCELLED: frozenset(),
    PaymentStatus.FAILED: frozenset(),
}


def can_transition(current: PaymentStatus, target: PaymentStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: PaymentStatus, target: PaymentStatus) -> None:
    if not can_transition(current, target):
        raise DomainRuleViolationError(
            "error.payments.invalid_transition",
            current=current.value,
            target=target.value,
            allowed=sorted(state.value for state in TRANSITIONS[current]),
        )


#: Providers occasionally settle a few kopeks off after currency handling. Anything
#: inside this is accepted; anything outside is a discrepancy a human must look at,
#: because silently accepting an underpayment is how a farm loses money slowly.
RECONCILIATION_TOLERANCE = Decimal("0.01")


def reconcile(expected: Decimal, captured: Decimal) -> None:
    """Refuse a settlement that does not match what was owed."""
    if abs(expected - captured) > RECONCILIATION_TOLERANCE:
        raise DomainRuleViolationError(
            "error.payments.amount_mismatch",
            expected=str(expected),
            captured=str(captured),
        )


def assert_refundable(*, settled: Decimal, already_refunded: Decimal, amount: Decimal) -> None:
    """A refund may never exceed what is still held."""
    if amount <= 0:
        raise DomainRuleViolationError("error.payments.refund_not_positive", amount=str(amount))
    remaining = settled - already_refunded
    if amount > remaining:
        raise DomainRuleViolationError(
            "error.payments.refund_exceeds_balance",
            requested=str(amount),
            remaining=str(remaining),
        )
