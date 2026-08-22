"""Orders: placing them, tracking them, moving them along.

Read the authorization carefully. A customer may see **their own** orders and
nobody else's; seeing the whole table needs a separate permission. That distinction
is enforced in the handler, not by trusting the client to ask nicely.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, status

from printorian.api.deps import (
    CurrentActor,
    DbSession,
    FarmSettings,
    Fleet,
    OptionalActor,
    Ordering,
    Production,
    requires,
)
from printorian.api.routers._cabinet_views import Machine, OrderProgress
from printorian.api.routers._line_pricing import spec_for
from printorian.api.routers._loyalty import tier_for
from printorian.api.routers._pricing_render import _render

# Shared with the pricing router so a finish cannot be priced one way in the
# preview and another at checkout.
from printorian.contexts.identity import Permission
from printorian.contexts.ordering import (
    OrderStatus,
    OrderTable,
    OrderView,
    PlaceOrder,
    RepriceLine,
)
from printorian.contexts.pricing import (
    price,
)
from printorian.core.errors import NotFoundError, PermissionDeniedError
from printorian.core.ids import EntityId

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def place_order(
    data: PlaceOrder,
    actor: CurrentActor,
    ordering: Ordering,
    db: DbSession,
    settings: FarmSettings,
) -> OrderView:
    """Place an order, priced server-side.

    The client sends the *configuration*, never a price. The material rate is read
    from the catalogue and the breakdown computed by the same engine the
    configurator previewed with, then pinned to the order (ADR-0002). A client that
    could name its own price would eventually name a lower one.
    """
    if not actor.can(Permission.PLACE_ORDER):
        raise PermissionDeniedError(
            "error.permission_denied", permission=Permission.PLACE_ORDER.value
        )

    # Collection is not a discount, it is the absence of a service: the engine
    # omits the shipping line rather than zeroing it. Before this the spec used
    # the default, so every order — collected or not — was priced with delivery.
    spec = await spec_for(db, data.lines[0], include_shipping=data.delivery.method.is_shipped)
    # Resolved once, here at the edge, and passed in — never fetched inside the
    # engine (ADR-0002). The order stores both the resulting breakdown and these
    # rates, so the quote can be rebuilt later rather than merely displayed.
    #
    # From the settings table now, with the code defaults underneath: an empty
    # table resolves to exactly the numbers this line used to construct. That the
    # snapshot is *pinned* here is what makes the farm safe to re-rate at all —
    # tomorrow's margin does not reach into today's agreed order (ADR-0020).
    rates = await settings.resolve_rates()
    # The loyalty discount, resolved from what this customer has already spent.
    # Resolved here rather than inside the engine, which is given its rates and
    # looks nothing up (ADR-0002) — and *before* the order is written, so the
    # order that creates a promotion is not itself priced at the new rate.
    tier = await tier_for(db, actor)
    return await ordering.place(data, price(spec, rates, tier), rates, customer_id=actor.user_id)


@router.post("/reprice")
async def reprice(
    data: RepriceLine, db: DbSession, actor: OptionalActor, settings: FarmSettings
) -> dict[str, Any]:
    """What this configuration costs with *this* delivery, without ordering it.

    The checkout needs it because the configurator cannot ask where the parts are
    going: it quotes with delivery included, and a customer who then picks
    collection would watch the total drop by several hundred roubles the moment
    they pressed the button, with nothing on screen explaining why.

    Runs the same spec builder the order does, so the figure shown here is the
    figure charged — the one thing a re-price must never get wrong. It takes only
    the delivery *method* though, not an address: the rate is flat, and demanding
    one would withhold the answer at exactly the moment the customer is deciding.

    Nothing is written and no permission is needed. It prices a configuration the
    caller already has, and refusing it to a signed-out visitor would only mean
    showing them a stale number.
    """
    spec = await spec_for(db, data.lines[0], include_shipping=data.method.is_shipped)
    rates = await settings.resolve_rates()
    return {"breakdown": _render(price(spec, rates, await tier_for(db, actor)))}


@router.get("/mine")
async def my_orders(
    actor: CurrentActor,
    ordering: Ordering,
    limit: int | None = None,
    cursor: str | None = None,
) -> OrderTable:
    """The customer cabinet.

    Scoped by the query itself, not by a filter parameter the caller could omit.
    """
    return await ordering.table(customer_id=actor.user_id, limit=limit, cursor=cursor)


@router.get("", dependencies=[Depends(requires(Permission.VIEW_ALL_ORDERS))])
async def orders_table(
    ordering: Ordering, limit: int | None = None, cursor: str | None = None
) -> OrderTable:
    """The admin orders screen: rows plus the status counts for the chips above.

    Paged. ``cursor`` comes from the previous response's ``next_cursor`` and is
    opaque; omitting it starts at the newest order. The counts always describe the
    whole table, not the page.
    """
    return await ordering.table(limit=limit, cursor=cursor)


@router.get("/overdue")
async def overdue_orders(
    ordering: Ordering,
    _: Annotated[object, Depends(requires(Permission.VIEW_ALL_ORDERS))] = None,
) -> list[OrderView]:
    """Past their promise and still owed work — what the SLA sweep watches."""
    return await ordering.overdue()


@router.get("/{order_id}")
async def get_order(order_id: EntityId, actor: CurrentActor, ordering: Ordering) -> OrderView:
    """One order, if the caller is entitled to it."""
    order = await ordering.get(order_id)
    if order.customer_id == actor.user_id or actor.can(Permission.VIEW_ALL_ORDERS):
        return order

    # Deliberately identical to a plain permission failure. Saying "that order
    # exists but is not yours" would let a stranger enumerate which orders exist.
    raise PermissionDeniedError(
        "error.permission_denied", permission=Permission.VIEW_ALL_ORDERS.value
    )


@router.get("/{order_id}/queue")
async def order_queue(
    order_id: EntityId,
    actor: CurrentActor,
    ordering: Ordering,
    production: Production,
    fleet: Fleet,
) -> OrderProgress:
    """Where this order's work stands, and on which machine.

    The scenario's C7. Guarded by the same ownership rule as the order itself:
    queue depth would otherwise let a stranger measure the farm's workload, and
    the refusal is deliberately identical to a plain permission failure so it
    cannot be used to discover which orders exist.

    Both halves may be absent and each means something different. No `queue` at
    all is an order with no job yet — paid, nothing created for it, a real state
    rather than an error. A queue with no `machine` is work that has not been
    given one, which is the ordinary case for anything still waiting.
    """
    order = await ordering.get(order_id)
    if not (order.customer_id == actor.user_id or actor.can(Permission.VIEW_ALL_ORDERS)):
        raise PermissionDeniedError(
            "error.permission_denied", permission=Permission.VIEW_ALL_ORDERS.value
        )

    queue = await production.queue_position(order_id)
    machine: Machine | None = None
    if queue is not None and queue.printer_id is not None:
        try:
            printer = await fleet.get(queue.printer_id)
        except NotFoundError:
            # Retired since the job was assigned. The pipeline still shows the
            # stage; it just cannot name what ran it, which beats failing the
            # whole panel over a machine the farm no longer owns.
            printer = None
        if printer is not None:
            machine = Machine(
                name=printer.name,
                brand=printer.brand,
                model=printer.model,
                state=printer.state.value,
                progress_percent=printer.progress_percent,
                remaining_minutes=printer.remaining_minutes,
                eta=printer.eta,
                layer_current=printer.layer_current,
                layer_total=printer.layer_total,
            )

    return OrderProgress(queue=queue, machine=machine)


@router.post("/{order_id}/advance", dependencies=[Depends(requires(Permission.MANAGE_ORDER))])
async def advance_order(
    order_id: EntityId,
    actor: CurrentActor,
    ordering: Ordering,
    target: Annotated[OrderStatus, Body(embed=True)],
    reason: Annotated[str, Body(embed=True)] = "",
) -> OrderView:
    """Move an order on, refusing anything the state machine disallows."""
    return await ordering.advance(order_id, target, reason=reason, actor_id=actor.user_id)
