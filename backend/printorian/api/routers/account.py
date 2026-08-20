"""The customer's own record: profile, standing, addresses, uploads, documents.

Every route here is scoped to the caller. Not by a filter the client passes — by
the query itself, which never sees an id from the request. That is the same rule
`/orders/mine` follows and for the same reason: a scope the client can name is a
scope the client can widen.

The screen this serves is `design/account.html`, whose seven sections span five
contexts. The composition happens here, in the delivery layer, because it is the
only layer allowed to know all five (`_account_views.py`).

Security lives in `_account_security.py` and the address book in
`_account_addresses.py` — separate modules, both mounted on this router, so
neither this file nor those grows past the length gate.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Response, status

from printorian.api.deps import (
    Account,
    AppClock,
    AppSettings,
    CurrentActor,
    DbSession,
    Identity,
    Journal,
    Models,
    Ordering,
    Payments,
)
from printorian.api.routers._account_addresses import router as addresses_router
from printorian.api.routers._account_security import router as security_router
from printorian.api.routers._account_views import Overview, Receipt, Shelf, ShelvedModel
from printorian.api.routers.catalog import safe_filename
from printorian.contexts.account import NotificationSettings, UpdateNotifications, tier_of
from printorian.contexts.identity import UpdateProfile, UserView
from printorian.contexts.ordering import lifetime, lines_per_asset, order_numbers, spent
from printorian.core.errors import NotFoundError
from printorian.core.ids import EntityId

router = APIRouter(prefix="/account", tags=["account"])

#: Rows per page when walking the order history for the CSV export.
_EXPORT_PAGE = 200


@router.get("")
async def overview(
    actor: CurrentActor, identity: Identity, db: DbSession, clock: AppClock
) -> Overview:
    """The identity plate, the loyalty ladder and the four lifetime figures.

    One request rather than three, because the header renders as a unit and three
    would let it paint in pieces. Each part still comes from the context that owns
    it — the profile from identity, the totals from ordering, the ladder from
    pricing by way of `account.tier_of`.
    """
    return Overview(
        profile=await identity.get_user(actor.user_id),
        tier=tier_of(await spent(db, actor.user_id)),
        lifetime=await lifetime(db, clock, actor.user_id),
    )


@router.patch("/profile")
async def edit_profile(data: UpdateProfile, actor: CurrentActor, identity: Identity) -> UserView:
    """Change your own name, phone, language or customer type.

    Not your email, your role or whether you are active. The first needs the new
    address proved before it becomes a login, which needs mail the farm cannot yet
    send; the other two are somebody else's decision and belong to `/users`.
    """
    return await identity.update_profile(actor.user_id, data)


@router.get("/notifications")
async def notifications(
    actor: CurrentActor, account: Account, journal: Journal
) -> NotificationSettings:
    """When to write. The journal row is a subscription, and comes from elsewhere."""
    return await account.notifications(
        actor.user_id, journal=await journal.is_subscribed(actor.email)
    )


@router.patch("/notifications")
async def set_notifications(
    data: UpdateNotifications, actor: CurrentActor, account: Account, journal: Journal
) -> NotificationSettings:
    """Apply a partial update.

    The journal switch is not a column here: it is a row in `journal_subscribers`,
    keyed by email address rather than by account, because most people who want a
    farm's reports do not have an account and should not need one. Applied against
    that context and reported back in the same object, so the panel stays one
    request.
    """
    if data.journal is not None:
        if data.journal:
            await journal.subscribe(actor.email)
        else:
            await journal.unsubscribe_email(actor.email)

    return await account.set_notifications(
        actor.user_id, data, journal=await journal.is_subscribed(actor.email)
    )


@router.get("/models")
async def shelf(actor: CurrentActor, models: Models, db: DbSession, settings: AppSettings) -> Shelf:
    """«Мои модели» — what this customer has uploaded, and how much room it takes.

    The quota is measured against, not enforced. Refusing an upload mid-quote
    because of a limit nobody has been shown is the wrong moment to enforce
    anything; this screen is where somebody can see which file to remove.
    """
    assets = await models.uploaded_by(actor.user_id)
    counted = await lines_per_asset(db, actor.user_id)
    return Shelf(
        models=[ShelvedModel(asset=asset, orders=counted.get(asset.id, 0)) for asset in assets],
        used_bytes=sum(asset.size_bytes for asset in assets),
        quota_bytes=settings.customer_storage_quota_bytes,
    )


@router.get("/models/{asset_id}/file", response_class=Response)
async def stored_geometry(asset_id: EntityId, actor: CurrentActor, models: Models) -> Response:
    """The bytes behind one of *your* uploads, for «Заказать» and for the 3D view.

    The catalogue's `/catalog/{slug}/model` is deliberately public — it serves
    geometry the farm has chosen to publish — and just as deliberately refuses to
    serve a customer's own file. This is the route that does, and the scoping is
    the whole of it: the asset is fetched, its `uploaded_by` is compared with the
    caller, and anything else answers *not found*.

    Not *forbidden* for somebody else's asset. Asset ids are content-derived only
    in their hash, not in their key, but a caller who can distinguish "yours" from
    "exists" can still probe the table.
    """
    asset = await models.get(asset_id)
    if asset.uploaded_by != actor.user_id:
        raise NotFoundError("error.catalog.asset_not_found", asset_id=str(asset_id))

    content, filename = await models.content(asset_id)
    return Response(
        content=content,
        media_type="model/stl",
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename(filename)}"',
            # Content-addressed storage, so the bytes behind an id never change.
            # `private`, unlike the catalogue's: this one is scoped to one person
            # and must not be held by a shared cache on the way.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/documents")
async def documents(actor: CurrentActor, db: DbSession, payments: Payments) -> list[Receipt]:
    """Receipts and refund notes, newest first.

    Scoped by fetching this customer's order ids first and asking payments about
    exactly those. Payments knows about orders and nothing about who placed them,
    which is right — teaching it to filter by customer would be teaching it to
    read another context's table.
    """
    numbers = await order_numbers(db, actor.user_id)
    rows = await payments.documents_for(list(numbers))
    return [
        Receipt(
            kind=row.kind,
            order_id=row.order_id,
            order_number=numbers.get(row.order_id, ""),
            provider=row.provider,
            amount=row.amount,
            currency=row.currency,
            issued_at=row.issued_at,
        )
        for row in rows
    ]


@router.get("/orders.csv")
async def orders_csv(actor: CurrentActor, ordering: Ordering) -> Response:
    """The whole order history as a spreadsheet — the kit's «Выгрузить историю CSV».

    Paged through rather than fetched at once: the table endpoint caps a page at
    two hundred, and an export that silently stopped at the cap would be an export
    that quietly loses the oldest orders of the customers who have most of them.

    A BOM, and `;` — because the overwhelmingly likely destination is Excel in a
    Russian locale, which reads a comma as a decimal separator and a BOM-less file
    as Windows-1251. Without both, every row lands in one column with the Cyrillic
    mangled. This is a rendered document, not an API response, so ADR-0012's
    "codes, never prose" does not apply: the header row is written for a person.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Номер", "Статус", "Создан", "Модель", "Количество", "Сумма", "Валюта"])

    cursor: str | None = None
    while True:
        page = await ordering.table(customer_id=actor.user_id, limit=_EXPORT_PAGE, cursor=cursor)
        for order in page.rows:
            line = order.lines[0] if order.lines else None
            writer.writerow(
                [
                    order.number,
                    order.status.value,
                    order.created_at.date().isoformat(),
                    line.model_name if line else "",
                    line.quantity if line else "",
                    f"{order.total:.2f}",
                    order.currency,
                ]
            )
        cursor = page.next_cursor
        if not cursor:
            break

    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="printorian-orders.csv"'},
    )


@router.get("/export")
async def export(
    actor: CurrentActor,
    identity: Identity,
    account: Account,
    ordering: Ordering,
    models: Models,
    payments: Payments,
    journal: Journal,
    db: DbSession,
) -> dict[str, Any]:
    """«Выгрузить мои данные» — everything the farm holds about this customer.

    Their profile, addresses, preferences, orders with the pinned breakdowns, the
    geometry they uploaded and the documents. Not the geometry *itself*: the files
    are tens of megabytes each and are downloadable one at a time from the model
    library, and inlining them would make this response the largest the API can
    produce.

    Assembled from the same services the screens use, so it can never describe a
    different farm from the one the customer has been looking at.
    """
    orders: list[Any] = []
    cursor: str | None = None
    while True:
        page = await ordering.table(customer_id=actor.user_id, limit=_EXPORT_PAGE, cursor=cursor)
        orders.extend(row.model_dump(mode="json") for row in page.rows)
        cursor = page.next_cursor
        if not cursor:
            break

    profile = await identity.get_user(actor.user_id)
    prefs = await account.notifications(
        actor.user_id, journal=await journal.is_subscribed(actor.email)
    )
    return {
        "profile": profile.model_dump(mode="json"),
        "addresses": [
            row.model_dump(mode="json") for row in await account.addresses(actor.user_id)
        ],
        "notifications": prefs.model_dump(mode="json"),
        "sessions": [
            row.model_dump(mode="json") for row in await identity.list_sessions(actor.user_id)
        ],
        "orders": orders,
        "models": [row.model_dump(mode="json") for row in await models.uploaded_by(actor.user_id)],
        "documents": [
            row.model_dump(mode="json")
            for row in await payments.documents_for(list(await order_numbers(db, actor.user_id)))
        ],
    }


@router.post("/close", status_code=status.HTTP_204_NO_CONTENT)
async def close_account(actor: CurrentActor, identity: Identity) -> None:
    """Close the account: sign-in stops working, everything else is kept.

    Exactly what the kit's own copy promises — «Удаление отключает вход; заказы и
    документы сохраняются». It is also the only version that is legal: the farm
    is obliged to keep what it billed for, and an order whose customer row had
    been deleted is an order nobody can be shown a receipt for.

    Deactivation revokes every live session as a side effect, so the tab this was
    pressed in stops working on its next request. That is the intended outcome and
    the reason there is no confirmation step here — the screen asks.
    """
    await identity.set_active(actor.user_id, is_active=False)


router.include_router(addresses_router)
router.include_router(security_router)
