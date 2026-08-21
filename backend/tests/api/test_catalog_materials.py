"""«Подходящие материалы» — one table, three contexts.

The catalogue says how well each material suits the part, inventory says what is
on the shelf, and the price difference is arithmetic over the two. Split from
`test_catalog_admin.py` because that file reached the 400-line gate, and because
this is a different question: not *who may edit* the library, but *what the popup
is allowed to claim*.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.contexts.identity import Role
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._catalog_support import (
    _TestDatabase,
    a_model,
    sign_in,
    upload,
)
from tests.conftest import wire_app


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    database = _TestDatabase(settings.database_url)

    wire_app(
        app,
        settings=settings,
        clock=clock,
        bus=bus,
        database=database,
        object_store=object_store,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http

    await database.dispose()


@pytest.fixture
async def editor(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> AsyncClient:
    """A client authenticated as somebody who may curate the library."""
    await sign_in(client, settings, clock, bus, Role.ENGINEER)
    return client


# ------------------------------------------------- «Подходящие материалы»


async def a_spec(settings: Settings, code: str, name: str, price_per_gram: str, grams: str) -> None:
    """A material the shop actually carries, with stock behind it."""
    from decimal import Decimal

    from printorian.contexts.inventory import (
        CreateMaterialLot,
        CreateMaterialSpec,
        InventoryService,
    )

    database = _TestDatabase(settings.database_url)
    async with database.session_factory() as session:
        inventory = InventoryService(session)
        await inventory.create_spec(
            CreateMaterialSpec(
                code=code,
                name=name,
                family=code.upper(),
                sell_price_per_gram=Decimal(price_per_gram),
                density_g_per_cm3=Decimal("1.24"),
            )
        )
        await inventory.add_lot(
            CreateMaterialLot(
                spec_code=code, initial_grams=Decimal(grams), remaining_grams=Decimal(grams)
            )
        )
        await session.commit()
    await database.dispose()


async def test_the_table_joins_the_catalogue_to_pricing_and_stock(
    editor: AsyncClient, settings: Settings
) -> None:
    """Three contexts, one row.

    The catalogue says how well each material suits the part; inventory says what
    is on the shelf; the price difference is arithmetic over the two.
    """
    await a_spec(settings, "pla", "PLA белый", "2.00", "12800")
    await a_spec(settings, "petg", "PETG чёрный", "3.00", "6800")
    asset = await upload(editor)
    await editor.post(
        "/catalog",
        json=a_model(
            asset,
            materials=[
                {"code": "pla", "suitability": "excellent", "is_recommended": True},
                {"code": "petg", "suitability": "good", "note": "Не для улицы"},
            ],
        ),
    )

    rows = (await editor.get("/catalog/test-bracket")).json()["suitable_materials"]

    assert [row["code"] for row in rows] == ["pla", "petg"], "recommended sorts first"
    # The family, not the colour. A catalogue entry is offered "in PLA"; the shop
    # stocks `pla-white` and `pla-black` separately, and the table sums them.
    assert rows[0]["name"] == "PLA"
    assert rows[0]["is_recommended"] is True
    assert float(rows[0]["stock_grams"]) == 12800.0
    assert rows[1]["note"] == "Не для улицы"
    assert rows[1]["suitability"] == "good"


async def test_no_measured_print_means_no_price_difference(
    editor: AsyncClient, settings: Settings
) -> None:
    """The column is empty rather than estimated.

    Δ price is the *material* cost difference on the mass the farm actually used.
    A model nobody has printed has no such mass, and inventing one would be the
    single fabricated figure on a screen whose whole claim is measurement.
    """
    await a_spec(settings, "pla", "PLA", "2.00", "1000")
    await a_spec(settings, "petg", "PETG", "3.00", "1000")
    asset = await upload(editor)
    await editor.post(
        "/catalog",
        json=a_model(
            asset,
            materials=[{"code": "pla", "is_recommended": True}, {"code": "petg"}],
        ),
    )

    rows = (await editor.get("/catalog/test-bracket")).json()["suitable_materials"]

    assert all(row["price_delta"] is None for row in rows)


async def test_a_material_the_shop_does_not_stock_still_appears(
    editor: AsyncClient, settings: Settings
) -> None:
    """ "We would print this in ASA" is true whether or not any is on the shelf.

    `stock_grams` is `None` — not zero, which would claim the shop carries it and
    has run out.
    """
    asset = await upload(editor)
    await editor.post("/catalog", json=a_model(asset, materials=[{"code": "asa"}]))

    rows = (await editor.get("/catalog/test-bracket")).json()["suitable_materials"]

    assert len(rows) == 1
    assert rows[0]["code"] == "asa"
    assert rows[0]["stock_grams"] is None


async def test_a_save_answers_with_the_same_shape_a_read_does(
    editor: AsyncClient, settings: Settings
) -> None:
    """Create, update and detail all carry the composed table.

    Without this an editor's form renders an empty materials table right after
    saving, and only fills it on the next load — which reads as the save having
    lost the judgements it just stored.
    """
    await a_spec(settings, "pla", "PLA", "2.00", "1000")
    asset = await upload(editor)

    created = await editor.post(
        "/catalog", json=a_model(asset, materials=[{"code": "pla", "is_recommended": True}])
    )
    patched = await editor.patch("/catalog/test-bracket", json={"title": "Другое имя"})
    fetched = await editor.get("/catalog/test-bracket")

    for response in (created, patched, fetched):
        codes = [row["code"] for row in response.json()["suitable_materials"]]
        assert codes == ["pla"], response.json()["suitable_materials"]


# ------------------------------------------------------ «Цена по количеству»


async def test_the_ladder_is_five_real_quotes(editor: AsyncClient, settings: Settings) -> None:
    """Each rung goes through the pricing engine, not one price extrapolated.

    Per-unit has to *fall* with quantity even though no volume discount is
    configured, because the per-job costs — plate setup, buying in a material the
    shop does not hold — spread across more units. That slope is the honest reason
    a ladder exists, and interpolating from a single quote would lose it.
    """
    await a_spec(settings, "pla", "PLA", "2.00", "12800")
    asset = await upload(editor)
    await editor.post(
        "/catalog", json=a_model(asset, materials=[{"code": "pla", "is_recommended": True}])
    )

    body = (await editor.get("/catalog/test-bracket")).json()
    ladder = body["price_ladder"]

    assert [rung["quantity"] for rung in ladder] == [1, 5, 10, 25, 50]
    units = [float(rung["unit_price"]) for rung in ladder]
    assert units == sorted(units, reverse=True), units
    # The total still rises — cheaper each, more of them.
    totals = [float(rung["total"]) for rung in ladder]
    assert totals == sorted(totals), totals
    basis = body["price_basis"]
    assert basis.startswith("PLA")
    # Every way this ladder differs from a configurator quote, named. The two are
    # one click apart now, and an unexplained gap reads as an error in one of them.
    assert "БЕЗ ОБРАБОТКИ И ДОСТАВКИ" in basis
    # The ladder prices the family's dearest colour, so the configurator can land
    # under this figure but never over it.
    assert "ВЕРХНЯЯ ГРАНИЦА" in basis


async def test_the_ladder_promises_a_floor_and_then_grows(
    editor: AsyncClient, settings: Settings
) -> None:
    """«Срок» never dips under the minimum lead, and scales with machine time.

    A twenty-minute print still has to be scheduled, started, taken off the bed
    and packed — see `ordering.promise`, whose constants belong in the settings
    store the kit describes.
    """
    await a_spec(settings, "pla", "PLA", "2.00", "12800")
    asset = await upload(editor)
    await editor.post(
        "/catalog", json=a_model(asset, materials=[{"code": "pla", "is_recommended": True}])
    )

    ladder = (await editor.get("/catalog/test-bracket")).json()["price_ladder"]

    leads = [float(rung["lead_hours"]) for rung in ladder]
    assert min(leads) >= 24.0, leads
    assert leads == sorted(leads), leads


async def test_a_model_with_no_stocked_material_has_no_ladder(editor: AsyncClient) -> None:
    """Empty rather than raising.

    A popup that refuses to open because one table cannot be computed is worse
    than one missing that table.
    """
    asset = await upload(editor)
    await editor.post("/catalog", json=a_model(asset, materials=[{"code": "unobtainium"}]))

    body = (await editor.get("/catalog/test-bracket")).json()

    assert body["price_ladder"] == []
    assert body["price_basis"] == ""
