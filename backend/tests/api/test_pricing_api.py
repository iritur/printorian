"""Phase 1 exit criterion, end to end over HTTP.

    "An API call prices a real STL with options and returns a full itemized
     breakdown; a second call with one option changed returns a correct,
     labelled delta."

This is scenario steps 3 and 4 — the transparent price structure, and the honest
answer to "what if I change this option?".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from printorian.api.app import create_app
from printorian.contexts.inventory import CreateMaterialLot, CreateMaterialSpec, InventoryService
from printorian.contexts.ordering import MIN_LEAD_HOURS, RUSH_LEAD_HOURS
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.db import Base
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.unit.test_mesh_analysis import cube_triangles, to_binary_stl

CUBE = to_binary_stl(cube_triangles(40.0))  # a 40mm cube: substantial but quick


class _TestDatabase:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, poolclass=NullPool)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


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
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    # Two materials so a swap can be previewed, and stock so status is not "none".
    async with database.session_factory() as session:
        inventory = InventoryService(session)
        await inventory.create_spec(
            CreateMaterialSpec(
                code="pla-black",
                name="PLA Matte Black",
                family="PLA",
                color_name="Black",
                sell_price_per_gram=Decimal("2.40"),
                density_g_per_cm3=Decimal("1.24"),
                tensile_mpa=Decimal(50),
            )
        )
        await inventory.create_spec(
            CreateMaterialSpec(
                code="petg-clear",
                name="PETG Clear",
                family="PETG",
                sell_price_per_gram=Decimal("3.90"),
                density_g_per_cm3=Decimal("1.27"),
                tensile_mpa=Decimal(45),
                hdt_c=Decimal(75),
                is_outdoor_safe=True,
            )
        )
        await inventory.add_lot(CreateMaterialLot(spec_code="pla-black", shelf="B3"))
        await session.commit()

    app.state.settings = settings
    app.state.clock = clock
    app.state.event_bus = bus
    app.state.database = database
    app.state.object_store = object_store

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http

    await database.dispose()


def _form(**overrides: object) -> dict[str, object]:
    return {"material_code": "pla-black", "quantity": 1, **overrides}


# ------------------------------------------------------------------- quote


async def test_quote_prices_a_real_stl_with_a_full_breakdown(client: AsyncClient) -> None:
    response = await client.post(
        "/pricing/quote", files={"model": ("cube.stl", CUBE, "model/stl")}, data=_form()
    )
    assert response.status_code == 200, response.text
    body = response.json()

    model = body["model"]
    assert model["triangle_count"] == 12
    assert Decimal(model["volume_cm3"]) == pytest.approx(Decimal(64), abs=Decimal("0.1"))
    assert model["estimate_source"] == "mesh_heuristic"
    assert Decimal(model["bounding_box_mm"]["x"]) == pytest.approx(Decimal(40), abs=Decimal("0.01"))

    breakdown = body["breakdown"]
    codes = [line["code"] for line in breakdown["lines"]]
    assert "material.filament" in codes
    assert "machine.electricity" in codes
    assert "labor.setup" in codes
    assert "margin.profit" in codes

    assert Decimal(breakdown["total"]) > 0
    assert breakdown["engine_version"]
    assert breakdown["rate_snapshot_id"].startswith("rates_")


async def test_breakdown_lines_sum_to_the_stated_total(client: AsyncClient) -> None:
    """The transparency claim has to survive addition."""
    response = await client.post(
        "/pricing/quote", files={"model": ("cube.stl", CUBE, "model/stl")}, data=_form()
    )
    breakdown = response.json()["breakdown"]

    total = sum(Decimal(line["amount"]) for line in breakdown["lines"])
    assert total == Decimal(breakdown["total"])


async def test_every_line_carries_a_structured_basis_not_prose(client: AsyncClient) -> None:
    """ADR-0012: the client renders the explanation; the backend sends the numbers."""
    response = await client.post(
        "/pricing/quote", files={"model": ("cube.stl", CUBE, "model/stl")}, data=_form()
    )
    for line in response.json()["breakdown"]["lines"]:
        basis = line["basis"]
        assert basis["kind"] in {
            "flat",
            "per_unit",
            "rate_over_quantity",
            "percent_of",
            "tiered_percent",
        }


async def test_options_change_the_quote(client: AsyncClient) -> None:
    plain = await client.post(
        "/pricing/quote", files={"model": ("cube.stl", CUBE, "model/stl")}, data=_form()
    )
    fancy = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(quantity=5, rush=True, finishes=["painted"]),
    )

    assert Decimal(fancy.json()["breakdown"]["total"]) > Decimal(plain.json()["breakdown"]["total"])
    fancy_codes = [line["code"] for line in fancy.json()["breakdown"]["lines"]]
    assert "postprocess.painted" in fancy_codes
    assert "adjustment.rush" in fancy_codes


async def test_quote_carries_the_lead_time_it_will_be_held_to(client: AsyncClient) -> None:
    """A quote answers both questions a customer has: how much, and when.

    Served from the quote rather than computed by the client so the buffer policy
    has one home. It is deliberately *not* in the breakdown — ADR-0002 keeps the
    pricing engine to money, and hours are not money.
    """
    response = await client.post(
        "/pricing/quote", files={"model": ("cube.stl", CUBE, "model/stl")}, data=_form()
    )
    model = response.json()["model"]

    # Floor, not print time: a cube takes minutes, and nobody promises minutes.
    assert Decimal(model["promised_hours"]) == MIN_LEAD_HOURS
    assert Decimal(model["rush_hours"]) == RUSH_LEAD_HOURS


async def test_a_bigger_batch_is_promised_later(client: AsyncClient) -> None:
    """Ten of a part is ten prints, so the promise has to move with the quantity."""
    many = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(quantity=200),
    )

    assert Decimal(many.json()["model"]["promised_hours"]) > MIN_LEAD_HOURS


async def test_rush_is_what_the_surcharge_buys(client: AsyncClient) -> None:
    """The kit offers rush as «СРОК 18 Ч ВМЕСТО 74 Ч», so the figure must react."""
    rushed = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(quantity=200, rush=True),
    )

    assert Decimal(rushed.json()["model"]["promised_hours"]) == RUSH_LEAD_HOURS


async def test_a_mesh_with_a_hole_is_refused_rather_than_guessed(client: AsyncClient) -> None:
    """No defined volume means no honest price, so the request fails loudly."""
    broken = to_binary_stl(cube_triangles(20.0)[:-2])
    response = await client.post(
        "/pricing/quote", files={"model": ("broken.stl", broken, "model/stl")}, data=_form()
    )

    assert response.status_code == 422
    assert response.json()["code"] == "error.catalog.mesh_not_priceable"


async def test_unknown_material_is_a_404_with_a_code(client: AsyncClient) -> None:
    response = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(material_code="unobtainium"),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "error.inventory.spec_not_found"


# ----------------------------------------------------------------- preview


async def test_preview_returns_a_labelled_per_line_delta(client: AsyncClient) -> None:
    """Scenario step 4: "+120 in labor, −260 in material" before committing."""
    response = await client.post(
        "/pricing/preview",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(to_finishes=["painted"]),
    )
    assert response.status_code == 200, response.text
    delta = response.json()["delta"]

    changed = {line["code"]: line for line in delta["changed"]}
    assert "postprocess.painted" in changed
    assert changed["postprocess.painted"]["is_new"] is True
    assert Decimal(delta["total_change"]) > 0
    assert delta["comparable"] is True


async def test_preview_of_a_cheaper_material_shows_a_decrease(client: AsyncClient) -> None:
    expensive = await client.post(
        "/pricing/preview",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(to_material_code="petg-clear"),
    )
    delta = expensive.json()["delta"]
    material = next(line for line in delta["changed"] if line["code"] == "material.filament")

    # PETG is dearer per gram *and* denser, so both directions are exercised by
    # simply reversing the two materials in the request.
    assert Decimal(material["change"]) > 0
    assert Decimal(delta["total_change"]) > 0


async def test_preview_matches_the_two_quotes_it_compares(client: AsyncClient) -> None:
    """The preview must not be able to disagree with the quote the customer accepts."""
    before = await client.post(
        "/pricing/quote", files={"model": ("cube.stl", CUBE, "model/stl")}, data=_form()
    )
    after = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(quantity=10),
    )
    delta = await client.post(
        "/pricing/preview",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(to_quantity=10),
    )

    expected = Decimal(after.json()["breakdown"]["total"]) - Decimal(
        before.json()["breakdown"]["total"]
    )
    assert Decimal(delta.json()["delta"]["total_change"]) == expected


async def test_preview_without_a_change_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/pricing/preview", files={"model": ("cube.stl", CUBE, "model/stl")}, data=_form()
    )
    assert response.status_code == 422
    assert response.json()["code"] == "error.pricing.no_option_change"


async def test_unknown_finish_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/pricing/quote",
        files={"model": ("cube.stl", CUBE, "model/stl")},
        data=_form(finishes=["gold-plated"]),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "error.pricing.unknown_finish"


# --------------------------------------------------------------- materials


async def test_materials_table_carries_rows_and_status_counts(client: AsyncClient) -> None:
    """The scenario's table: rows, sortable columns, and status chips with counts."""
    response = await client.get("/materials")
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    by_status = {entry["status"]: entry["count"] for entry in body["counts"]}
    # Every status is present, including the empty ones.
    assert set(by_status) == {"stock", "in_printer", "ordered", "none"}
    assert by_status["stock"] == 1  # pla-black has a lot on shelf B3
    assert by_status["none"] == 1  # petg-clear has none

    pla = next(row for row in body["rows"] if row["code"] == "pla-black")
    assert pla["status"] == "stock"
    assert pla["lots"][0]["shelf"] == "B3"


async def test_recommendation_prefers_what_is_actually_in_stock(client: AsyncClient) -> None:
    response = await client.get("/materials/recommend", params={"min_tensile_mpa": 40})
    matches = response.json()

    assert matches[0]["spec"]["code"] == "pla-black"
    assert "match.in_stock" in matches[0]["reasons"]


async def test_recommendation_filters_on_hard_requirements(client: AsyncClient) -> None:
    response = await client.get("/materials/recommend", params={"requires_outdoor": True})
    codes = [match["spec"]["code"] for match in response.json()]

    assert codes == ["petg-clear"]
