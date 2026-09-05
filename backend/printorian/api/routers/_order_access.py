"""One definition of "this order is mine, or I am staff who may see it".

The rule was written out at each route that needed it, and by the fourth copy the
payments router had simply not written it at all — four routes there took an id
and answered without ever asking whose order it was. A rule that has to be
remembered at every call site is a rule that will be forgotten at one of them.

Which staff permission counts is the caller's decision and not a default worth
having, because the two answers in the codebase are genuinely different questions:

* `VIEW_ALL_ORDERS` for what an order *is* — its lines, its status, its queue.
* `VIEW_FINANCIALS` for what it cost. A `PaymentView` is rubles end to end, and
  CLAUDE.md §1 keeps money out of the production permissions so a response that
  carries minutes cannot quietly start carrying rubles.

Customer access is the same on both: the order is theirs, or it is not.
"""

from __future__ import annotations

from printorian.contexts.identity import Actor, Permission
from printorian.contexts.ordering import OrderingService, OrderView
from printorian.core.errors import PermissionDeniedError
from printorian.core.ids import EntityId


async def order_for(
    ordering: OrderingService,
    order_id: EntityId,
    actor: Actor,
    *,
    staff_permission: Permission,
) -> OrderView:
    """Load the order, or refuse — and hand the order back, since callers need it.

    An order that does not exist raises `NotFoundError` from the service, which is
    a 404 rather than an empty answer: an all-empty response reads as "this order
    took no payments", and that is a claim about an order the farm never had
    (ADR-0007).

    The refusal is deliberately identical to a plain permission failure. Saying
    "that order exists but is not yours" would let a stranger enumerate which
    orders exist by watching 403 turn into 404.
    """
    order = await ordering.get(order_id)
    if order.customer_id == actor.user_id or actor.can(staff_permission):
        return order

    raise PermissionDeniedError("error.permission_denied", permission=staff_permission.value)
