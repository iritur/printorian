"""The model library: keeping an upload, finding it again, and letting it go.

This is the half of DB-1 that the folder cannot do on its own. The bytes live on
disk; what is tested here is everything that needs a row — identity by content, the
digest `plate_key` is built on, and a retention rule that cannot collect geometry an
order still has to print.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import ModelFormat, ModelLibrary, assert_priceable, plate_key
from printorian.contexts.catalog.models import ModelAsset
from printorian.contexts.ordering.models import Order, OrderLine
from printorian.core.clock import FixedClock
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import new_id
from printorian.core.storage import InMemoryObjectStore, digest_of

#: A watertight unit cube in ASCII STL. Small enough to read, real enough to parse.
CUBE = b"""solid cube
facet normal 0 0 -1
 outer loop
  vertex 0 0 0
  vertex 1 1 0
  vertex 1 0 0
 endloop
endfacet
facet normal 0 0 -1
 outer loop
  vertex 0 0 0
  vertex 0 1 0
  vertex 1 1 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 0 0 1
  vertex 1 0 1
  vertex 1 1 1
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 0 0 1
  vertex 1 1 1
  vertex 0 1 1
 endloop
endfacet
facet normal 0 -1 0
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 1 0 1
 endloop
endfacet
facet normal 0 -1 0
 outer loop
  vertex 0 0 0
  vertex 1 0 1
  vertex 0 0 1
 endloop
endfacet
facet normal 1 0 0
 outer loop
  vertex 1 0 0
  vertex 1 1 0
  vertex 1 1 1
 endloop
endfacet
facet normal 1 0 0
 outer loop
  vertex 1 0 0
  vertex 1 1 1
  vertex 1 0 1
 endloop
endfacet
facet normal 0 1 0
 outer loop
  vertex 1 1 0
  vertex 0 1 0
  vertex 0 1 1
 endloop
endfacet
facet normal 0 1 0
 outer loop
  vertex 1 1 0
  vertex 0 1 1
  vertex 1 1 1
 endloop
endfacet
facet normal -1 0 0
 outer loop
  vertex 0 1 0
  vertex 0 0 0
  vertex 0 0 1
 endloop
endfacet
facet normal -1 0 0
 outer loop
  vertex 0 1 0
  vertex 0 0 1
  vertex 0 1 1
 endloop
endfacet
endsolid cube
"""


@pytest.fixture
def models(
    db_session: AsyncSession, object_store: InMemoryObjectStore, clock: FixedClock
) -> ModelLibrary:
    return ModelLibrary(db_session, object_store, clock)


# ------------------------------------------------------------- ingesting


async def test_an_upload_is_stored_and_measured(
    models: ModelLibrary, object_store: InMemoryObjectStore
) -> None:
    asset = await models.ingest(CUBE, filename="cube.stl")

    assert asset.sha256 == digest_of(CUBE)
    assert asset.original_filename == "cube.stl"
    assert asset.format is ModelFormat.STL
    assert asset.size_bytes == len(CUBE)
    assert await object_store.get(asset.sha256) == CUBE


async def test_the_geometry_is_measured_once_and_kept(models: ModelLibrary) -> None:
    """The configurator re-uploads the same file on every option change. Storing
    the analysis is what stops the server re-parsing it each time."""
    asset = await models.ingest(CUBE, filename="cube.stl")

    assert asset.triangle_count == 12
    assert asset.is_watertight
    assert asset.volume_cm3 > 0
    assert asset.width_mm == Decimal("1.00")
    assert asset.mesh["measured"] is True
    # A 1 mm cube is all thin wall, so the analysis warns. Kept as the assertion
    # because a *warning* is still priceable — only an unclosed mesh is not.
    assert asset.mesh["quality"] == "warning"
    assert asset.is_priceable


async def test_the_same_bytes_are_one_asset(models: ModelLibrary, db_session: AsyncSession) -> None:
    """Identity is the content, not the filename.

    Two customers uploading the same part under different names share one asset —
    and therefore, through `plate_key`, one prepared plate.
    """
    first = await models.ingest(CUBE, filename="cube.stl")
    second = await models.ingest(CUBE, filename="totally-different-name.stl")

    assert first.id == second.id
    assert await db_session.scalar(select(func.count()).select_from(ModelAsset)) == 1


async def test_different_geometry_under_one_name_is_two_assets(models: ModelLibrary) -> None:
    """The converse, and the reason a filename cannot be the identity."""
    first = await models.ingest(CUBE, filename="part.stl")
    second = await models.ingest(CUBE.replace(b"solid cube", b"solid cubf"), filename="part.stl")

    assert first.id != second.id


async def test_re_ingesting_refreshes_the_retention_clock(
    models: ModelLibrary, clock: FixedClock
) -> None:
    """Retention counts from the last use, so a model reprinted every month is
    never collected while an experiment from last year is."""
    first = await models.ingest(CUBE, filename="cube.stl")
    clock.advance(timedelta(days=200))
    again = await models.ingest(CUBE, filename="cube.stl")

    assert again.id == first.id
    assert again.last_used_at == clock.now()


async def test_an_unreadable_format_is_stored_but_not_priceable(models: ModelLibrary) -> None:
    """ADR-0007's rule applied to geometry: a 3MF is kept and served, but it has
    not been measured, so nothing may quote against it. Zeroed measurements
    presented as real ones would be V1's mistake in a new place."""
    asset = await models.ingest(b"PK\x03\x04not-really-a-3mf", filename="thing.3mf")

    assert asset.format is ModelFormat.THREE_MF
    assert asset.mesh == {"measured": False}
    assert not asset.is_priceable
    with pytest.raises(ValidationError):
        assert_priceable(asset)


# --------------------------------------------------------------- reading


async def test_the_bytes_come_back_under_their_original_name(models: ModelLibrary) -> None:
    """What the console hands an engineer. A directory of digests is unusable to a
    person, so the customer's filename travels with the bytes."""
    asset = await models.ingest(CUBE, filename="bracket-v3.stl")

    content, filename = await models.content(asset.id)

    assert content == CUBE
    assert filename == "bracket-v3.stl"


async def test_an_unknown_asset_is_not_found(models: ModelLibrary) -> None:
    with pytest.raises(NotFoundError):
        await models.get(new_id())


async def test_an_asset_is_findable_by_its_digest(models: ModelLibrary) -> None:
    """How a client that kept the hash asks "have we got this?" without uploading
    the file again."""
    asset = await models.ingest(CUBE, filename="cube.stl")

    assert (await models.by_hash(asset.sha256)).id == asset.id
    assert await models.by_hash(digest_of(b"something else")) is None


async def test_the_digest_is_what_the_plate_cache_is_keyed_on(models: ModelLibrary) -> None:
    """The link that makes ADR-0006 work at all.

    `plate_key` consumes `model_hash`; that value is this asset's digest. Without
    it the cache can never hit and every repeat order goes back through an engineer.
    """
    asset = await models.ingest(CUBE, filename="cube.stl")

    key = plate_key(
        model_hash=asset.sha256,
        scale=Decimal(1),
        material_code="pla-black",
        printer_profile="p1s-0.4",
    )
    same = plate_key(
        model_hash=digest_of(CUBE),
        scale=Decimal(1),
        material_code="pla-black",
        printer_profile="p1s-0.4",
    )
    assert key == same


# ------------------------------------------------------------- retention


async def test_an_unused_model_is_collected(
    models: ModelLibrary, object_store: InMemoryObjectStore, clock: FixedClock
) -> None:
    """Every quote nobody ordered kept forever fills the farm's disk."""
    asset = await models.ingest(CUBE, filename="cube.stl")
    clock.advance(timedelta(days=200))

    assert await models.purge_unused(older_than=timedelta(days=180)) == 1
    assert await object_store.exists(asset.sha256) is False


async def test_a_recently_used_model_is_kept(models: ModelLibrary, clock: FixedClock) -> None:
    await models.ingest(CUBE, filename="cube.stl")
    clock.advance(timedelta(days=10))

    assert await models.purge_unused(older_than=timedelta(days=180)) == 0


async def test_a_model_an_order_still_needs_is_never_collected(
    models: ModelLibrary,
    db_session: AsyncSession,
    object_store: InMemoryObjectStore,
    clock: FixedClock,
) -> None:
    """The guarantee that makes retention safe, and it is the foreign key rather
    than a query: `order_lines.model_asset_id` is `RESTRICT`, so the database
    refuses to collect geometry an order still has to print. Asking in SQL instead
    would mean `catalog` knowing that `ordering` exists, and would race with an
    order placed mid-sweep.
    """
    asset = await models.ingest(CUBE, filename="cube.stl")
    order = Order(number="PR-000001", customer_email="buyer@example.com")
    order.lines.append(
        OrderLine(
            model_name="cube.stl",
            model_asset_id=asset.id,
            material_code="pla-black",
            estimated_minutes=Decimal(10),
            estimated_grams=Decimal(10),
        )
    )
    db_session.add(order)
    await db_session.flush()

    clock.advance(timedelta(days=400))
    collected = await models.purge_unused(older_than=timedelta(days=180))

    assert collected == 0
    assert await models.by_hash(asset.sha256) is not None
    # The bytes are still there too — a surviving row naming a deleted file would
    # be the worse half of the failure.
    assert await object_store.exists(asset.sha256) is True


async def test_collecting_one_model_does_not_stop_at_a_protected_one(
    models: ModelLibrary, db_session: AsyncSession, clock: FixedClock
) -> None:
    """Each candidate is deleted in its own savepoint, so a referenced model is an
    answer rather than an error that abandons the rest of the sweep."""
    protected = await models.ingest(CUBE, filename="ordered.stl")
    loose = await models.ingest(CUBE + b"\n", filename="abandoned-quote.stl")

    order = Order(number="PR-000002", customer_email="buyer@example.com")
    order.lines.append(
        OrderLine(
            model_name="ordered.stl",
            model_asset_id=protected.id,
            material_code="pla-black",
            estimated_minutes=Decimal(10),
            estimated_grams=Decimal(10),
        )
    )
    db_session.add(order)
    await db_session.flush()

    clock.advance(timedelta(days=400))
    assert await models.purge_unused(older_than=timedelta(days=180)) == 1
    assert await models.by_hash(protected.sha256) is not None
    assert await models.by_hash(loose.sha256) is None
