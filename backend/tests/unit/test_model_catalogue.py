"""Browsing the model library.

The load-bearing behaviour is the facet algebra — **OR within a group, AND across
groups** — and the promise that search, facets and sort are one pass. Both are
easy to break in a way no type checker notices and every reader of the screen
notices immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog import (
    CatalogModel,
    CatalogModelMaterial,
    Facets,
    ModelCatalogue,
    ModelCategory,
    SizeClass,
    SortKey,
    card_of,
    size_class_of,
)
from printorian.contexts.catalog.models import ModelAsset, ModelFormat
from printorian.core.errors import NotFoundError
from printorian.core.ids import new_id


async def an_asset(session: AsyncSession, *, volume: str = "41.3") -> ModelAsset:
    asset = ModelAsset(
        id=new_id(),
        sha256=new_id().hex,
        original_filename="part.stl",
        format=ModelFormat.STL,
        size_bytes=1024,
        storage_path=f"aa/{new_id().hex}",
        triangle_count=1000,
        volume_cm3=Decimal(volume),
        width_mm=Decimal(128),
        depth_mm=Decimal(64),
        height_mm=Decimal(22),
        is_watertight=True,
        mesh={},
        last_used_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(asset)
    await session.flush()
    return asset


async def a_model(
    session: AsyncSession,
    *,
    slug: str,
    title: str = "Кронштейн",
    code: str = "BRACKET_V4",
    category: ModelCategory = ModelCategory.FUNCTIONAL,
    size: SizeClass = SizeClass.MEDIUM,
    difficulty: int = 6,
    multicolor: bool = False,
    materials: tuple[str, ...] = ("pla",),
    print_count: int = 0,
    rating: tuple[int, int] = (0, 0),
    published: bool = True,
    measured: bool = False,
    volume: str = "41.3",
) -> CatalogModel:
    asset = await an_asset(session, volume=volume)
    model = CatalogModel(
        id=new_id(),
        slug=slug,
        code=code,
        title=title,
        summary="",
        category=category,
        size_class=size,
        difficulty=difficulty,
        multicolor=multicolor,
        tags=[],
        model_asset_id=asset.id,
        rating_sum=rating[0],
        rating_count=rating[1],
        print_count=print_count,
        is_published=published,
        published_at=datetime(2026, 7, 12, tzinfo=UTC) if published else None,
        preview={},
        last_printed_at=datetime(2026, 8, 9, tzinfo=UTC) if measured else None,
        last_print_minutes=Decimal(141) if measured else None,
        last_print_grams=Decimal("41.3") if measured else None,
        last_price=Decimal(601) if measured else None,
        last_printer_name="P-01" if measured else "",
    )
    session.add(model)
    await session.flush()
    for code_ in materials:
        session.add(CatalogModelMaterial(model_id=model.id, material_code=code_))
    await session.flush()
    # Load the relationships explicitly. `card_of` reads both, and a freshly
    # flushed instance has neither populated — touching one would trigger lazy IO
    # from sync context, which is a test failure that says nothing about the code
    # under test.
    await session.refresh(model, attribute_names=["materials", "asset"])
    return model


@pytest.fixture
def catalogue(db_session: AsyncSession) -> ModelCatalogue:
    return ModelCatalogue(db_session)


# ---------------------------------------------------------------- size policy


@pytest.mark.parametrize(
    ("dims", "expected"),
    [
        ((Decimal(20), Decimal(20), Decimal(20)), SizeClass.SMALL),
        # Boundaries belong to the smaller class, so the kit's «До 50 мм» reads as
        # it is written rather than being off by one at the only value anyone
        # checks.
        ((Decimal(50), Decimal(10), Decimal(10)), SizeClass.SMALL),
        ((Decimal(51), Decimal(10), Decimal(10)), SizeClass.MEDIUM),
        ((Decimal(150), Decimal(10), Decimal(10)), SizeClass.MEDIUM),
        ((Decimal(151), Decimal(10), Decimal(10)), SizeClass.LARGE),
        # The *longest edge* decides, not the volume: a long thin bracket is a big
        # print even though it displaces almost nothing.
        ((Decimal(10), Decimal(10), Decimal(400)), SizeClass.LARGE),
    ],
)
def test_size_class_is_decided_by_the_longest_edge(
    dims: tuple[Decimal, Decimal, Decimal], expected: SizeClass
) -> None:
    assert size_class_of(*dims) is expected


# --------------------------------------------------------------- facet algebra


async def test_two_materials_widen_the_result_rather_than_narrowing_it(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """OR within a group.

    Ticking a second material must add models, not remove them. Getting this
    backwards produces a facet list where every extra tick empties the screen,
    which is the single most common way a catalogue is wrong.
    """
    await a_model(db_session, slug="a", materials=("pla",))
    await a_model(db_session, slug="b", materials=("petg",))
    await a_model(db_session, slug="c", materials=("asa",))

    only_pla = await catalogue.search(facets=Facets(materials=frozenset({"pla"})))
    both = await catalogue.search(facets=Facets(materials=frozenset({"pla", "petg"})))

    assert only_pla.total == 1
    assert both.total == 2


async def test_a_model_offered_in_two_selected_materials_appears_once(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """The join has to not multiply rows.

    A model offered in both PLA and PETG matches the material filter twice. If
    that is a join rather than a subquery, it appears twice in the grid and the
    total is wrong — visible immediately, and invisible in any single-material
    test.
    """
    await a_model(db_session, slug="both", materials=("pla", "petg"))

    page = await catalogue.search(facets=Facets(materials=frozenset({"pla", "petg"})))

    assert page.total == 1
    assert [model.slug for model in page.rows] == ["both"]


async def test_groups_are_combined_with_and(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """AND across groups: "PLA or PETG, **and** small"."""
    await a_model(db_session, slug="small-pla", size=SizeClass.SMALL, materials=("pla",))
    await a_model(db_session, slug="large-pla", size=SizeClass.LARGE, materials=("pla",))
    await a_model(db_session, slug="small-asa", size=SizeClass.SMALL, materials=("asa",))

    page = await catalogue.search(
        facets=Facets(sizes=frozenset({SizeClass.SMALL}), materials=frozenset({"pla"}))
    )

    assert [model.slug for model in page.rows] == ["small-pla"]


async def test_both_colour_boxes_ticked_constrains_nothing(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """Selecting every option in a group is the same as selecting none."""
    await a_model(db_session, slug="one", multicolor=False)
    await a_model(db_session, slug="multi", multicolor=True)

    both = await catalogue.search(facets=Facets(multicolor=frozenset({True, False})))
    neither = await catalogue.search(facets=Facets())

    assert both.total == neither.total == 2


async def test_difficulty_bands_cover_the_scale_without_overlapping(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    await a_model(db_session, slug="easy", difficulty=2)
    await a_model(db_session, slug="mid", difficulty=6)
    await a_model(db_session, slug="hard", difficulty=9)

    for band, slug in (("easy", "easy"), ("mid", "mid"), ("hard", "hard")):
        page = await catalogue.search(facets=Facets(difficulties=frozenset({band})))
        assert [model.slug for model in page.rows] == [slug], band


# ------------------------------------------------------------- one pass, sort


async def test_search_and_facets_and_sort_apply_together(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """The screen's central promise.

    Sorting must not clear the filter and searching must not ignore it. A search
    that quietly dropped the facet would return the ASA row too.
    """
    pla = {"materials": ("pla",)}
    await a_model(db_session, slug="b1", title="Кронштейн малый", print_count=5, **pla)
    await a_model(db_session, slug="b2", title="Кронштейн большой", print_count=9, **pla)
    await a_model(db_session, slug="b3", title="Кронштейн ASA", materials=("asa",), print_count=99)

    page = await catalogue.search(
        text="кронштейн",
        facets=Facets(materials=frozenset({"pla"})),
        sort=SortKey.PRINTS,
    )

    assert [model.slug for model in page.rows] == ["b2", "b1"]
    assert page.total == 2


async def test_quality_keys_open_descending_and_cost_keys_ascending(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """The first click shows what the reader is looking for.

    Cheapest first when sorting by cost; best first when sorting by quality.
    """
    await a_model(db_session, slug="cheap", difficulty=1, rating=(30, 10), print_count=1)
    await a_model(db_session, slug="dear", difficulty=9, rating=(50, 10), print_count=9)

    by_rating = await catalogue.search(sort=SortKey.RATING)
    by_difficulty = await catalogue.search(sort=SortKey.DIFFICULTY)

    assert [model.slug for model in by_rating.rows] == ["dear", "cheap"]
    assert [model.slug for model in by_difficulty.rows] == ["cheap", "dear"]


async def test_clicking_the_active_key_flips_it(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    await a_model(db_session, slug="low", print_count=1)
    await a_model(db_session, slug="high", print_count=9)

    descending = await catalogue.search(sort=SortKey.PRINTS, descending=True)
    ascending = await catalogue.search(sort=SortKey.PRINTS, descending=False)

    assert [model.slug for model in descending.rows] == ["high", "low"]
    assert [model.slug for model in ascending.rows] == ["low", "high"]


async def test_unrated_models_sort_predictably(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """A null mean must not order dialect-dependently.

    SQLite and PostgreSQL disagree about where nulls sort, so an unrated model
    left as null would pass this suite and be wrong in production.
    """
    await a_model(db_session, slug="rated", rating=(48, 10))
    await a_model(db_session, slug="unrated", rating=(0, 0))

    page = await catalogue.search(sort=SortKey.RATING)

    assert [model.slug for model in page.rows] == ["rated", "unrated"]


async def test_paging_is_stable_when_the_sort_key_ties(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """Every row appears exactly once across pages.

    With no tiebreaker, rows that tie can swap between pages and the reader sees
    one twice and another never.
    """
    for index in range(6):
        await a_model(db_session, slug=f"tied-{index}", print_count=3)

    first = await catalogue.search(sort=SortKey.PRINTS, limit=3, offset=0)
    second = await catalogue.search(sort=SortKey.PRINTS, limit=3, offset=3)

    seen = [model.slug for model in first.rows] + [model.slug for model in second.rows]
    assert len(set(seen)) == 6


# ------------------------------------------------------------- facet counts


async def test_a_chip_counts_what_ticking_it_would_give(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """Counts drop their own group's filter.

    With PLA ticked, `PETG` must still show how many models ticking it *as well*
    would add. Counting with every filter applied shows `PETG 0`, and the reader
    can never discover that the option does anything.
    """
    await a_model(db_session, slug="p1", materials=("pla",))
    await a_model(db_session, slug="p2", materials=("petg",))
    await a_model(db_session, slug="p3", materials=("petg",))

    page = await catalogue.search(facets=Facets(materials=frozenset({"pla"})))
    counts = {entry.value: entry.count for entry in page.counts["mat"]}

    assert page.total == 1
    assert counts == {"pla": 1, "petg": 2}


async def test_counts_from_other_groups_still_narrow(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    """Dropping *its own* group is not the same as dropping every filter."""
    await a_model(db_session, slug="s-pla", size=SizeClass.SMALL, materials=("pla",))
    await a_model(db_session, slug="l-petg", size=SizeClass.LARGE, materials=("petg",))

    page = await catalogue.search(facets=Facets(sizes=frozenset({SizeClass.SMALL})))
    counts = {entry.value: entry.count for entry in page.counts["mat"]}

    # PETG exists, but not among small models — so its chip reads zero, honestly.
    assert counts.get("pla") == 1
    assert counts.get("petg", 0) == 0


# --------------------------------------------------- published, and the claim


async def test_drafts_are_invisible_to_the_shop_window(
    db_session: AsyncSession, catalogue: ModelCatalogue
) -> None:
    await a_model(db_session, slug="live", published=True)
    await a_model(db_session, slug="draft", published=False)

    public = await catalogue.search()
    editor = await catalogue.search(include_unpublished=True)

    assert [model.slug for model in public.rows] == ["live"]
    assert {model.slug for model in editor.rows} == {"live", "draft"}

    with pytest.raises(NotFoundError):
        await catalogue.get("draft")
    assert (await catalogue.get("draft", include_unpublished=True)).slug == "draft"


async def test_a_model_nobody_has_printed_reports_no_measurement(db_session: AsyncSession) -> None:
    """The catalogue's whole claim is that its numbers are measured.

    A model with no completed print must say so, so the storefront can label its
    estimate as an estimate. Filling these from a prediction would be ADR-0007's
    defect moved into the catalogue.
    """
    never = await a_model(db_session, slug="never", measured=False)
    printed = await a_model(db_session, slug="printed", measured=True)

    assert card_of(never).measured is None

    measured = card_of(printed).measured
    assert measured is not None
    assert measured.minutes == Decimal(141)
    assert measured.printer_name == "P-01"


async def test_a_card_reads_its_geometry_from_the_asset(db_session: AsyncSession) -> None:
    """Geometry has one home, so a card cannot disagree with the mesh it names."""
    model = await a_model(db_session, slug="geo", volume="96.2")

    card = card_of(model)

    assert card.volume_cm3 == Decimal("96.2")
    assert (card.width_mm, card.depth_mm, card.height_mm) == (
        Decimal(128),
        Decimal(64),
        Decimal(22),
    )
