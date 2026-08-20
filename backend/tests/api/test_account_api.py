"""The account screen over HTTP.

What these are for is the scoping. Every route on `/account` reads the caller out
of the session and never out of the request, so most of the cases below are one
customer failing to see or touch another's record — the boundary an attacker
actually hits, rather than the button the screen chooses not to draw.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.contexts.account import MAX_ADDRESSES
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import PASSWORD, a_shop, place, token_for


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async for http in a_shop(object_store, settings, clock, bus):
        yield http


def _an_address(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "label": "Дом",
        "city": "Москва",
        "postcode": "101000",
        "address": "ул. Мясницкая, д. 12, кв. 45",
    }
    return base | changes


# ------------------------------------------------------------------ the door


async def test_the_account_is_closed_to_anonymous_callers(client: AsyncClient) -> None:
    for path in ("/account", "/account/addresses", "/account/sessions", "/account/export"):
        assert (await client.get(path)).status_code == 401, path


# ---------------------------------------------------------------- the header


async def test_the_header_reports_a_new_customer_as_measured_nothing(
    client: AsyncClient,
) -> None:
    """Zero orders, zero spend — and *absent* averages rather than zeroed ones.

    A customer with nothing dispatched has no average lead time. Reporting one as
    `0` would put «0.0 СУТ» on a screen that means it as a measurement.
    """
    auth = await token_for(client, "buyer@example.com")

    body = (await client.get("/account", headers=auth)).json()

    assert body["profile"]["email"] == "buyer@example.com"
    assert body["lifetime"]["orders"] == 0
    assert body["lifetime"]["average_order"] is None
    assert body["lifetime"]["average_days"] is None
    assert body["tier"]["code"] == "standard"
    assert len(body["lifetime"]["months"]) == 12


async def test_an_unpaid_order_is_counted_but_not_spent(client: AsyncClient) -> None:
    """«Заказов всего» counts what the customer placed; «Потрачено» counts money
    that moved. An order awaiting payment is one of the first and none of the
    second, and collapsing the two would have the header claim a customer had
    paid for something the moment they pressed the button."""
    auth = await token_for(client, "buyer@example.com")
    await place(client, auth)

    body = (await client.get("/account", headers=auth)).json()

    assert body["lifetime"]["orders"] == 1
    assert body["lifetime"]["spend"] == "0"
    assert body["lifetime"]["average_order"] is None
    # Nothing dispatched, so still no lead time to report.
    assert body["lifetime"]["average_days"] is None


async def test_one_customers_orders_do_not_reach_another(client: AsyncClient) -> None:
    buyer = await token_for(client, "buyer@example.com")
    await place(client, buyer)

    rival = (
        await client.get("/account", headers=await token_for(client, "rival@example.com"))
    ).json()

    assert rival["lifetime"]["orders"] == 0
    assert rival["lifetime"]["spend"] == "0"


# --------------------------------------------------------------- the profile


async def test_a_customer_may_change_their_own_name_and_phone(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")

    response = await client.patch(
        "/account/profile",
        json={"display_name": "Дмитрий Чудинов", "phone": "+7 916 000-00-00"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Дмитрий Чудинов"
    assert response.json()["phone"] == "+7 916 000-00-00"


async def test_the_profile_form_cannot_promote_anybody(client: AsyncClient) -> None:
    """`role` is not a field on `UpdateProfile`, so it is dropped rather than obeyed.

    The one that matters: an unknown key on a self-service form must never be a
    way to become an owner."""
    auth = await token_for(client, "buyer@example.com")

    response = await client.patch(
        "/account/profile", json={"display_name": "me", "role": "owner"}, headers=auth
    )

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "customer"


# ------------------------------------------------------------- the addresses


async def test_addresses_are_saved_listed_and_scoped(client: AsyncClient) -> None:
    buyer = await token_for(client, "buyer@example.com")
    rival = await token_for(client, "rival@example.com")

    created = await client.post("/account/addresses", json=_an_address(), headers=buyer)
    assert created.status_code == 201, created.text
    assert created.json()["is_default"] is True

    assert len((await client.get("/account/addresses", headers=buyer)).json()) == 1
    assert (await client.get("/account/addresses", headers=rival)).json() == []


async def test_another_customer_cannot_touch_an_address(client: AsyncClient) -> None:
    buyer = await token_for(client, "buyer@example.com")
    rival = await token_for(client, "rival@example.com")
    saved = (await client.post("/account/addresses", json=_an_address(), headers=buyer)).json()

    assert (
        await client.delete(f"/account/addresses/{saved['id']}", headers=rival)
    ).status_code == 404
    assert (
        await client.post(f"/account/addresses/{saved['id']}/default", headers=rival)
    ).status_code == 404
    # …and it is still there for the person who owns it.
    assert len((await client.get("/account/addresses", headers=buyer)).json()) == 1


async def test_an_address_without_a_city_is_refused(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")

    response = await client.post("/account/addresses", json=_an_address(city="   "), headers=auth)

    assert response.status_code == 422, response.text


async def test_the_address_book_stops_at_the_limit(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    for index in range(MAX_ADDRESSES):
        assert (
            await client.post(
                "/account/addresses", json=_an_address(label=f"#{index}"), headers=auth
            )
        ).status_code == 201

    response = await client.post("/account/addresses", json=_an_address(), headers=auth)

    assert response.status_code == 422
    assert response.json()["code"] == "error.account.too_many_addresses"


# --------------------------------------------------------- the notifications


async def test_notifications_round_trip(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")

    before = (await client.get("/account/notifications", headers=auth)).json()
    assert before["on_every_stage"] is False
    assert before["journal"] is False

    after = (
        await client.patch("/account/notifications", json={"on_every_stage": True}, headers=auth)
    ).json()

    assert after["on_every_stage"] is True
    assert (await client.get("/account/notifications", headers=auth)).json()[
        "on_every_stage"
    ] is True


async def test_the_journal_switch_writes_to_the_subscription_list(client: AsyncClient) -> None:
    """It is not a column on `notification_prefs`: the journal is a list of
    addresses, so that people without accounts can be on it."""
    auth = await token_for(client, "buyer@example.com")

    on = (await client.patch("/account/notifications", json={"journal": True}, headers=auth)).json()
    assert on["journal"] is True

    off = (
        await client.patch("/account/notifications", json={"journal": False}, headers=auth)
    ).json()
    assert off["journal"] is False


# -------------------------------------------------------------- the security


async def test_the_session_list_marks_the_one_making_the_request(
    client: AsyncClient,
) -> None:
    auth = await token_for(client, "buyer@example.com")

    rows = (await client.get("/account/sessions", headers=auth)).json()

    assert len(rows) == 1
    assert rows[0]["is_current"] is True


async def test_ending_the_others_spares_this_one(client: AsyncClient) -> None:
    """The exception is the whole feature. Ending everything would sign the
    customer out of the screen they are tidying up from."""
    first = await token_for(client, "buyer@example.com")
    await token_for(client, "buyer@example.com")
    await token_for(client, "buyer@example.com")
    assert len((await client.get("/account/sessions", headers=first)).json()) == 3

    ended = (await client.delete("/account/sessions", headers=first)).json()

    assert ended == {"ended": 2}
    rows = (await client.get("/account/sessions", headers=first)).json()
    assert [row["is_current"] for row in rows] == [True]


async def test_a_session_belonging_to_somebody_else_cannot_be_ended(
    client: AsyncClient,
) -> None:
    buyer = await token_for(client, "buyer@example.com")
    rival = await token_for(client, "rival@example.com")
    theirs = (await client.get("/account/sessions", headers=buyer)).json()[0]

    response = await client.delete(f"/account/sessions/{theirs['id']}", headers=rival)

    assert response.status_code == 404
    assert len((await client.get("/account/sessions", headers=buyer)).json()) == 1


async def test_changing_the_password_ends_every_session(client: AsyncClient) -> None:
    """Half the reason anybody changes a password is that they think somebody
    else knows it, and that is worthless if the sessions it opened survive."""
    auth = await token_for(client, "buyer@example.com")

    response = await client.post(
        "/account/password",
        json={"current": PASSWORD, "replacement": "a-much-better-secret"},
        headers=auth,
    )

    assert response.status_code == 204, response.text
    assert (await client.get("/account", headers=auth)).status_code == 401
    signed_in = await client.post(
        "/auth/sign-in",
        json={"email": "buyer@example.com", "password": "a-much-better-secret"},
    )
    assert signed_in.status_code == 200


async def test_the_wrong_current_password_changes_nothing(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")

    response = await client.post(
        "/account/password",
        json={"current": "not-it", "replacement": "a-much-better-secret"},
        headers=auth,
    )

    assert response.status_code == 401
    assert (await client.get("/account", headers=auth)).status_code == 200


# ------------------------------------------------- models, documents, export


async def test_the_shelf_is_empty_and_says_what_the_allowance_is(
    client: AsyncClient, settings: Settings
) -> None:
    auth = await token_for(client, "buyer@example.com")

    body = (await client.get("/account/models", headers=auth)).json()

    assert body["models"] == []
    assert body["used_bytes"] == 0
    assert body["quota_bytes"] == settings.customer_storage_quota_bytes


async def test_documents_appear_only_once_money_has_moved(client: AsyncClient) -> None:
    """An order that has been placed is not a receipt. A started-and-abandoned
    payment is not one either — only a settled one is."""
    auth = await token_for(client, "buyer@example.com")
    await place(client, auth)

    assert (await client.get("/account/documents", headers=auth)).json() == []


async def test_the_csv_export_is_a_spreadsheet_excel_can_open(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    order = await place(client, auth)

    response = await client.get("/account/orders.csv", headers=auth)

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    # A BOM and semicolons, or Excel in a Russian locale mangles the Cyrillic and
    # puts every row in one column.
    assert body.startswith("﻿")
    assert "Номер;Статус" in body
    assert str(order["number"]) in body


async def test_the_export_carries_the_whole_record(client: AsyncClient) -> None:
    auth = await token_for(client, "buyer@example.com")
    await client.post("/account/addresses", json=_an_address(), headers=auth)
    await place(client, auth)

    body = (await client.get("/account/export", headers=auth)).json()

    assert body["profile"]["email"] == "buyer@example.com"
    assert len(body["addresses"]) == 1
    assert len(body["orders"]) == 1
    assert body["notifications"]["on_paid"] is True
    assert body["sessions"]


async def test_closing_the_account_stops_the_login_and_keeps_the_orders(
    client: AsyncClient,
) -> None:
    """Exactly what the screen promises: «Удаление отключает вход; заказы и
    документы сохраняются»."""
    auth = await token_for(client, "buyer@example.com")
    await place(client, auth)

    assert (await client.post("/account/close", headers=auth)).status_code == 204

    assert (await client.get("/account", headers=auth)).status_code == 401
    refused = await client.post(
        "/auth/sign-in", json={"email": "buyer@example.com", "password": PASSWORD}
    )
    assert refused.status_code == 401

    # The orders are still there for the farm — an owner can still see them.
    staff = await token_for(client, "boss@example.com")
    assert (await client.get("/orders", headers=staff)).json()["total"] == 1
