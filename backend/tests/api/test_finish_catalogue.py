"""Editing «Постобработка» changes what a customer is quoted and charged.

This is the test that matters for [#29](https://github.com/iritur/printorian/issues/29),
and it is separate from `tests/unit/test_settings_tables.py` for a reason worth
stating: every assertion in that file passes with the routers still reading the
`FINISH_CATALOGUE` constant. A parser that refuses the wrong rows and a resolver
that returns the right ones prove nothing at all about the price, because the
price is computed four function calls away, at edges that used to read a module
constant. So this file changes the setting over HTTP and then drives the real
consumers.

The second thing it pins is the *asymmetry* around a finish code the catalogue
does not price, which a reviewer would otherwise read as an inconsistency. A
quoting edge refuses it, because the customer is still choosing and the farm does
not sell it. A repricing edge prices it at nothing and answers, because the line
already exists — refusing there would mean an owner tidying the catalogue could
stop a placed order from being repriced, and retroactively change what it cost.

A new file rather than more cases in `test_pricing_api.py` (353 lines) or
`test_settings_api.py` (362): both are close enough to the 400-line gate that this
would have tripped it, and the seam is real — those two ask what an endpoint does,
this one asks whether two endpoints agree.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any, cast

import pytest
from httpx import AsyncClient

from printorian.contexts.pricing import FINISH_CATALOGUE, RateSnapshot
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._checkout_support import a_shop, an_order_payload, token_for
from tests.unit.test_mesh_analysis import cube_triangles, to_binary_stl

CATALOGUE = "postprocess.operations"
CUBE = to_binary_stl(cube_triangles(40.0))  # a priceable 40mm cube

#: The norm-hours the farm sets sanding to, well clear of the 0.4 it ships with so
#: a figure that failed to move is visible rather than a rounding argument.
SANDED_HOURS = Decimal("0.9")

#: What one sanded unit therefore costs. Derived from the rate the farm is running
#: rather than typed as a number: `postprocess_rate_per_hour` is itself a setting,
#: and a hard-coded 450 here would start failing for the wrong reason the day its
#: default moved.
SANDED_PER_UNIT = SANDED_HOURS * RateSnapshot().postprocess_rate_per_hour


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async for shop in a_shop(object_store, settings, clock, bus):
        yield shop


def a_catalogue(**edits: dict[str, str]) -> list[dict[str, object]]:
    """The four operations as the PUT body wants them, with named rows edited.

    All four every time, because the code set is closed: the console sends the
    whole table back and `_parse_finishes` refuses a body that has added or
    dropped a code.
    """
    rows: list[dict[str, object]] = []
    for code, finish in FINISH_CATALOGUE.items():
        row: dict[str, object] = {
            "code": code,
            "labor_hours": str(finish.labor_hours),
            "flat_fee": str(finish.flat_fee),
            "extra_days": finish.extra_days,
        }
        row.update(edits.get(code, {}))
        rows.append(row)
    return rows


async def set_sanding_to(client: AsyncClient, hours: Decimal) -> None:
    auth = await token_for(client, "boss@example.com")
    saved = await client.put(
        f"/settings/{CATALOGUE}",
        json={"value": a_catalogue(sanded={"labor_hours": str(hours)})},
        headers=auth,
    )
    assert saved.status_code == 200, saved.text


def a_line(*finishes: str) -> dict[str, object]:
    """One configured line, quantity one, with the finishes named.

    Quantity one on purpose: the postprocess line is `per-unit rate x quantity`,
    and this file compares a figure from the configurator's preview against one
    from the checkout's reprice. At quantity one the two are directly comparable
    without either side dividing, which is the arithmetic a test should not be
    doing on the way to its own expected answer.
    """
    lines = cast(list[dict[str, Any]], an_order_payload()["lines"])
    line = dict(lines[0])
    line["quantity"] = 1
    line["finishes"] = list(finishes)
    return line


def line_named(breakdown: dict[str, Any], code: str) -> dict[str, Any] | None:
    return next((line for line in breakdown["lines"] if line["code"] == code), None)


async def reprice(client: AsyncClient, *finishes: str) -> dict[str, Any]:
    response = await client.post(
        "/orders/reprice", json={"method": "pickup", "lines": [a_line(*finishes)]}
    )
    assert response.status_code == 200, response.text
    return response.json()["breakdown"]


# ------------------------------------------------- the setting reaches the money


async def test_editing_the_catalogue_moves_what_the_checkout_reprices(
    client: AsyncClient,
) -> None:
    """The whole point of #29: the table an owner edits is the price they charge.

    Before the read edges moved off `FINISH_CATALOGUE` this passed the settings
    round trip and failed here — the row saved, the audit recorded it, the screen
    showed «БЫЛО», and sanding still cost 0.4 h.
    """
    shipped_hours = FINISH_CATALOGUE["sanded"].labor_hours
    at_default = line_named(await reprice(client, "sanded"), "postprocess.sanded")
    assert at_default is not None
    assert Decimal(at_default["amount"]) == shipped_hours * RateSnapshot().postprocess_rate_per_hour

    await set_sanding_to(client, SANDED_HOURS)

    edited = line_named(await reprice(client, "sanded"), "postprocess.sanded")
    assert edited is not None
    assert Decimal(edited["amount"]) == SANDED_PER_UNIT
    # The per-unit rate on the basis as well as the amount. `Basis.rate` is where
    # the applied finish rate is recoverable from a *pinned* breakdown, which is
    # the whole of ADR-0020's amendment for this change: what was charged stays
    # answerable after the catalogue moves on.
    assert Decimal(edited["basis"]["rate"]) == SANDED_PER_UNIT


async def test_the_configurator_and_the_checkout_quote_the_same_finish(
    client: AsyncClient,
) -> None:
    """The failure `_line_pricing.py` exists about, stated as an assertion.

    `POST /pricing/preview` is the configurator asking what sanding would cost;
    `POST /orders/reprice` is the checkout. They reach the catalogue by different
    routes — `_build_spec` and `spec_for` — so moving one of them onto the
    settings table and not the other is a farm that quotes one number and charges
    another. Both are driven here after the same edit.
    """
    await set_sanding_to(client, SANDED_HOURS)

    previewed = await client.post(
        "/pricing/preview",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data={"material_code": "pla-black", "quantity": 1, "to_finishes": ["sanded"]},
    )
    assert previewed.status_code == 200, previewed.text
    changed = {line["code"]: line for line in previewed.json()["delta"]["changed"]}
    at_configurator = Decimal(changed["postprocess.sanded"]["after"])

    at_checkout = line_named(await reprice(client, "sanded"), "postprocess.sanded")
    assert at_checkout is not None

    assert at_configurator == Decimal(at_checkout["amount"]) == SANDED_PER_UNIT


async def test_the_order_charges_what_the_checkout_showed(client: AsyncClient) -> None:
    """The edited catalogue reaches `POST /orders`, not only the preview of it."""
    await set_sanding_to(client, SANDED_HOURS)
    buyer = await token_for(client, "buyer@example.com")

    shown = await reprice(client, "sanded")
    payload = {**an_order_payload(), "lines": [a_line("sanded")]}
    charged = await client.post("/orders", json=payload, headers=buyer)

    assert charged.status_code == 201, charged.text
    assert Decimal(charged.json()["total"]) == Decimal(shown["total"])
    assert Decimal(shown["total"]) > Decimal((await reprice(client))["total"])


# --------------------------------------------- refused when chosen, kept when sold


async def test_an_unknown_finish_is_refused_at_the_quote_but_tolerated_when_repricing(
    client: AsyncClient,
) -> None:
    """One rule, two sides, and the asymmetry is the point.

    A code the catalogue does not price must not be quotable: the farm does not
    sell it, and `_build_spec` says so with `error.pricing.unknown_finish`. The
    same code arriving at a repricing edge is a line that already exists — an
    order placed under a finish the owner has since stopped offering — and
    refusing it there would mean tidying the catalogue retroactively changed what
    a placed order costs. So it prices at nothing and answers.
    """
    refused = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data={"material_code": "pla-black", "quantity": 1, "finishes": ["gold-plated"]},
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "error.pricing.unknown_finish"
    assert refused.json()["details"]["finishes"] == ["gold-plated"]

    tolerated = line_named(await reprice(client, "gold-plated"), "postprocess.gold-plated")

    assert tolerated is not None
    # Zero because the catalogue has no rate for it — and a zero here is the
    # honest answer rather than an invented one (ADR-0007): the farm charged for
    # this finish under a row that no longer exists, and the alternative to zero
    # is refusing to reprice the order at all.
    assert Decimal(tolerated["amount"]) == Decimal(0)
