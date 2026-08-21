"""Curating the model library — and who may.

The catalogue is the one screen with a public read side and a staff-only write
side on the same router. That split is the thing worth testing: a shop window
anybody can look into, whose contents only the farm can change.
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
    a_cube,
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


# ------------------------------------------------------------ who may write


async def test_an_anonymous_visitor_may_read_the_catalogue(client: AsyncClient) -> None:
    """The shop window is open. That is the whole point of a shop window."""
    assert (await client.get("/catalog")).status_code == 200


@pytest.mark.parametrize("path", ["/catalog", "/catalog/geometry"])
async def test_an_anonymous_visitor_may_not_write(client: AsyncClient, path: str) -> None:
    response = await client.post(path, json={})
    assert response.status_code == 401, response.text


async def test_a_customer_may_not_curate(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """Signed in is not the same as entitled.

    A customer holds `place_order` and nothing else; the catalogue is the farm's
    to describe.
    """
    await sign_in(client, settings, clock, bus, Role.CUSTOMER)

    response = await client.post(
        "/catalog/geometry", files={"file": ("cube.stl", a_cube(), "model/stl")}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "error.permission_denied"


async def test_an_engineer_may_curate(
    client: AsyncClient, settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """`MANAGE_LIBRARY` belongs to Engineer, Manager and Owner."""
    await sign_in(client, settings, clock, bus, Role.ENGINEER)
    assert (
        await client.post("/catalog/geometry", files={"file": ("cube.stl", a_cube(), "model/stl")})
    ).status_code == 201


# ------------------------------------------------------------------ the work


async def test_an_upload_answers_with_what_it_measured(editor: AsyncClient) -> None:
    """The form shows the farm's own measurements before anything is published."""
    response = await editor.post(
        "/catalog/geometry", files={"file": ("cube.stl", a_cube(40), "model/stl")}
    )

    body = response.json()
    assert body["triangle_count"] == 12
    # Compared numerically: the wire carries a decimal string whose trailing
    # zeros are an artefact of the column, not a fact about the part.
    assert (float(body["width_mm"]), float(body["height_mm"])) == (40.0, 40.0)
    assert body["is_watertight"] is True
    assert body["is_priceable"] is True
    # 40mm cube → longest edge 40 → small, by `size_class_of`.
    assert body["size_class"] == "s"


async def test_a_created_model_derives_its_own_size_and_drawing(editor: AsyncClient) -> None:
    """Neither is taken from the request, however it is spelled.

    A form that could set `size_class` would let the size facet disagree with the
    mesh it filters, and no screen could reveal it.
    """
    asset = await upload(editor)

    response = await editor.post("/catalog", json=a_model(asset))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["size_class"] == "s"
    assert len(body["preview"]["paths"]) >= 9
    assert float(body["volume_cm3"]) == 64.0


async def test_a_duplicate_slug_is_refused(editor: AsyncClient) -> None:
    """The slug is in a URL somebody may share; two models cannot own one."""
    asset = await upload(editor)
    assert (await editor.post("/catalog", json=a_model(asset))).status_code == 201

    response = await editor.post("/catalog", json=a_model(asset))

    assert response.status_code == 409
    assert response.json()["code"] == "error.catalog.slug_taken"


async def test_an_edit_leaves_absent_fields_alone(editor: AsyncClient) -> None:
    """`exclude_unset`: posting a title must not blank the description."""
    asset = await upload(editor)
    await editor.post("/catalog", json=a_model(asset, summary="Исходное описание"))

    response = await editor.patch("/catalog/test-bracket", json={"title": "Новое имя"})

    body = response.json()
    assert body["title"] == "Новое имя"
    assert body["summary"] == "Исходное описание"


async def test_materials_are_replaced_wholesale_and_deduplicated(editor: AsyncClient) -> None:
    asset = await upload(editor)
    await editor.post("/catalog", json=a_model(asset))

    response = await editor.patch(
        "/catalog/test-bracket",
        json={"materials": [{"code": "asa"}, {"code": "asa"}, {"code": "tpu"}]},
    )

    assert response.json()["materials"] == ["asa", "tpu"]


async def test_a_draft_is_invisible_until_published(
    editor: AsyncClient, client: AsyncClient
) -> None:
    """A model appears in the window when somebody says so, not when it is saved."""
    asset = await upload(editor)
    await editor.post("/catalog", json=a_model(asset, is_published=False))

    # The editor sees their own draft…
    assert (await editor.get("/catalog")).json()["total"] == 1
    # …and the same request from a signed-out client does not.
    await editor.post("/auth/sign-out")
    del editor.headers["Authorization"]
    assert (await editor.get("/catalog")).json()["total"] == 0
    assert (await editor.get("/catalog/test-bracket")).status_code == 404


async def test_republishing_keeps_the_original_publication_date(editor: AsyncClient) -> None:
    """Un-publishing and re-publishing does not make a model new.

    The catalogue sorts by this date, so a toggle would send an old part to the
    top of "Новизна".
    """
    asset = await upload(editor)
    created = await editor.post("/catalog", json=a_model(asset, is_published=True))
    first = created.json()["published_at"]

    await editor.patch("/catalog/test-bracket", json={"is_published": False})
    again = await editor.patch("/catalog/test-bracket", json={"is_published": True})

    assert again.json()["published_at"] == first


async def test_deleting_an_entry_leaves_the_mesh_behind(editor: AsyncClient) -> None:
    """Retention collects geometry, not an editor pressing delete.

    The asset is content-addressed and may already be referenced by a placed
    order, which is not this screen's business.
    """
    asset = await upload(editor)
    await editor.post("/catalog", json=a_model(asset))

    assert (await editor.delete("/catalog/test-bracket")).status_code == 204
    assert (await editor.get("/catalog")).json()["total"] == 0
    # The same upload still resolves to the same asset, so the bytes survived.
    assert await upload(editor) == asset
