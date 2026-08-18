"""Browsing the model library: search, facets and sort, in one pass.

The kit is explicit about the failure this avoids. Search, facets and sort feed a
**single** query, so sorting never clears a filter and searching never ignores the
facets — the classic catalogue bug, where three independent widgets each reset the
other two.

Facets are **OR within a group, AND across groups**: "PLA or PETG, and small".
That is not a preference, it is what makes a facet list usable — narrowing within
a group would mean ticking a second material could only ever return fewer models,
which is the opposite of what a reader means by ticking it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import Numeric, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from printorian.contexts.catalog.catalogue import (
    CatalogModel,
    CatalogModelMaterial,
    ModelCategory,
    SizeClass,
)
from printorian.contexts.catalog.models import ModelAsset
from printorian.core.errors import NotFoundError


class SortKey(StrEnum):
    """The kit's eight sort keys."""

    POPULAR = "popular"
    PRICE = "price"
    TIME = "time"
    VOLUME = "volume"
    DIFFICULTY = "difficulty"
    RATING = "rating"
    PRINTS = "prints"
    DATE = "date"


#: Which way a key opens.
#:
#: Cost-like keys open ascending and quality-like keys open descending, because
#: the first click should show what the reader is looking for: cheapest first,
#: best-rated first. Clicking the active key flips it — the table-header gesture,
#: already learned elsewhere in this app.
DESCENDING_BY_DEFAULT: frozenset[SortKey] = frozenset(
    {SortKey.POPULAR, SortKey.RATING, SortKey.PRINTS, SortKey.DATE}
)


@dataclass(frozen=True, slots=True)
class Facets:
    """One tick-box group each. Empty means "no constraint from this group"."""

    categories: frozenset[ModelCategory] = frozenset()
    sizes: frozenset[SizeClass] = frozenset()
    materials: frozenset[str] = frozenset()
    #: `True` selects multi-colour models, `False` single-colour. Both selected is
    #: the same as neither, and is normalised to no constraint.
    multicolor: frozenset[bool] = frozenset()
    #: Difficulty bands, as the kit's easy / mid / hard.
    difficulties: frozenset[str] = frozenset()


#: The kit's three difficulty bands over the 0–10 scale, as closed ranges.
DIFFICULTY_BANDS: dict[str, tuple[int, int]] = {
    "easy": (0, 3),
    "mid": (4, 7),
    "hard": (8, 10),
}


@dataclass(frozen=True, slots=True)
class FacetCount:
    value: str
    count: int


@dataclass(slots=True)
class CatalogPage:
    """One screenful, plus what the facet chips need to show their counts."""

    rows: list[CatalogModel] = field(default_factory=list)
    total: int = 0
    #: Per group, so each chip can print its own number.
    counts: dict[str, list[FacetCount]] = field(default_factory=dict)


class ModelCatalogue:
    """Read side of the model library."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get(self, slug: str, *, include_unpublished: bool = False) -> CatalogModel:
        """One model by its public slug."""
        query = select(CatalogModel).where(CatalogModel.slug == slug)
        if not include_unpublished:
            query = query.where(CatalogModel.is_published.is_(True))
        model = await self._db.scalar(query)
        if model is None:
            raise NotFoundError("error.model_not_found", details={"slug": slug})
        return model

    async def search(
        self,
        *,
        text: str = "",
        facets: Facets | None = None,
        sort: SortKey = SortKey.POPULAR,
        descending: bool | None = None,
        limit: int = 24,
        offset: int = 0,
        include_unpublished: bool = False,
    ) -> CatalogPage:
        """Search, facets and sort — one pass, as the screen promises."""
        facets = facets or Facets()

        base = select(CatalogModel)
        if not include_unpublished:
            base = base.where(CatalogModel.is_published.is_(True))
        base = _apply_text(base, text)
        base = _apply_facets(base, facets)

        total = await self._db.scalar(select(func.count()).select_from(base.subquery()))

        rows = await self._db.scalars(
            _apply_sort(base, sort, descending).limit(limit).offset(offset)
        )

        return CatalogPage(
            rows=list(rows.unique()),
            total=int(total or 0),
            counts=await self._counts(text, facets, include_unpublished),
        )

    async def _counts(
        self, text: str, facets: Facets, include_unpublished: bool
    ) -> dict[str, list[FacetCount]]:
        """Chip counts, each computed with **its own group's filter removed**.

        This is the part that is easy to get wrong and obvious once it is wrong: a
        count computed with every filter applied shows `PETG 0` the moment PLA is
        ticked, so the reader can never see what ticking PETG as well would give
        them. Since groups are ORed internally, the honest number for a chip is
        "how many match everything *except* this group".
        """
        counts: dict[str, list[FacetCount]] = {}

        def others(drop: str) -> Facets:
            return Facets(
                categories=frozenset() if drop == "cat" else facets.categories,
                sizes=frozenset() if drop == "size" else facets.sizes,
                materials=frozenset() if drop == "mat" else facets.materials,
                multicolor=frozenset() if drop == "colors" else facets.multicolor,
                difficulties=frozenset() if drop == "diff" else facets.difficulties,
            )

        def scoped(drop: str) -> Select[tuple[CatalogModel]]:
            query = select(CatalogModel)
            if not include_unpublished:
                query = query.where(CatalogModel.is_published.is_(True))
            return _apply_facets(_apply_text(query, text), others(drop))

        counts["cat"] = await self._count_by(scoped("cat"), "category")
        counts["size"] = await self._count_by(scoped("size"), "size_class")
        counts["colors"] = await self._count_by(scoped("colors"), "multicolor")

        # Materials count from the join side: one model offered in three materials
        # contributes to three chips, which is what the reader is being told.
        material_rows = await self._db.execute(
            select(CatalogModelMaterial.material_code, func.count())
            .join(CatalogModel, CatalogModel.id == CatalogModelMaterial.model_id)
            .where(CatalogModel.id.in_(scoped("mat").with_only_columns(CatalogModel.id)))
            .group_by(CatalogModelMaterial.material_code)
        )
        counts["mat"] = sorted(
            (FacetCount(value=str(code), count=int(n)) for code, n in material_rows),
            key=lambda entry: entry.value,
        )

        # Difficulty is banded, so it is counted band by band rather than by value
        # — eleven chips for a 0–10 scale is not a facet, it is a slider nobody
        # asked for.
        band_query = scoped("diff")
        counts["diff"] = [
            FacetCount(
                value=band,
                count=int(
                    await self._db.scalar(
                        select(func.count()).select_from(
                            band_query.where(CatalogModel.difficulty.between(low, high)).subquery()
                        )
                    )
                    or 0
                ),
            )
            for band, (low, high) in DIFFICULTY_BANDS.items()
        ]
        return counts

    async def _count_by(
        self, query: Select[tuple[CatalogModel]], column_name: str
    ) -> list[FacetCount]:
        """`GROUP BY` one column of the scoped set.

        Takes a *name* and resolves it against the subquery's own columns rather
        than taking `CatalogModel.category` directly. Grouping by the mapped
        attribute while selecting from a subquery leaves the entity table
        unreferenced in the FROM, which SQLAlchemy correctly reports as an
        accidental cartesian product — and this project runs with warnings as
        errors, so it fails loudly instead of quietly counting the whole table.
        """
        scoped = query.subquery("scoped")
        column = scoped.c[column_name]
        rows = await self._db.execute(
            select(column, func.count()).select_from(scoped).group_by(column)
        )
        return sorted(
            (FacetCount(value=_facet_value(value), count=int(n)) for value, n in rows),
            key=lambda entry: entry.value,
        )


def _facet_value(value: object) -> str:
    """A facet value as the wire spells it."""
    if isinstance(value, bool):
        return "multi" if value else "1"
    if isinstance(value, StrEnum):
        return value.value
    return str(value)


def _apply_text(query: Select[tuple[CatalogModel]], text: str) -> Select[tuple[CatalogModel]]:
    """Free-text search over title, code and tags.

    Matches the pre-folded `search_text` column rather than calling `lower()` in
    SQL: SQLite's `lower()` is ASCII-only, so a Cyrillic title would match in
    PostgreSQL and not in the tests — a green suite over a broken search box. Both
    sides of the comparison are folded by Python, which is Unicode-aware
    everywhere.

    `LIKE` rather than full-text search: the library is hundreds of rows and the
    reader is typing a part name they already half-know. A tsvector here would be
    a dialect-specific index for a problem that does not exist yet; when it does,
    this is the one function that changes.
    """
    needle = text.strip().lower()
    if not needle:
        return query
    return query.where(CatalogModel.search_text.like(f"%{needle}%"))


def _apply_facets(
    query: Select[tuple[CatalogModel]], facets: Facets
) -> Select[tuple[CatalogModel]]:
    """OR within a group, AND across groups."""
    if facets.categories:
        query = query.where(CatalogModel.category.in_(facets.categories))
    if facets.sizes:
        query = query.where(CatalogModel.size_class.in_(facets.sizes))
    # Both boxes ticked constrains nothing — it is every model — so it is dropped
    # rather than turned into `IN (true, false)`, which would say the same thing
    # more expensively.
    if len(facets.multicolor) == 1:
        query = query.where(CatalogModel.multicolor.is_(next(iter(facets.multicolor))))
    if facets.difficulties:
        bands = [DIFFICULTY_BANDS[band] for band in facets.difficulties if band in DIFFICULTY_BANDS]
        if bands:
            query = query.where(
                or_(*(CatalogModel.difficulty.between(low, high) for low, high in bands))
            )
    if facets.materials:
        # `IN (subquery)` rather than a join: a model offered in both PLA and PETG
        # would otherwise match twice and appear twice in the grid.
        query = query.where(
            CatalogModel.id.in_(
                select(CatalogModelMaterial.model_id).where(
                    CatalogModelMaterial.material_code.in_(facets.materials)
                )
            )
        )
    return query


def _mean_rating() -> ColumnElement[Any]:
    """Mean rating as SQL, with "nobody has rated it" as zero rather than null.

    `nullif` guards the division; `coalesce` turns the resulting null back into a
    sortable zero. Without the second half an unrated model sorts to whichever end
    the dialect happens to put nulls, which differs between SQLite and PostgreSQL
    — a test that passes and a screen that is wrong.
    """
    return func.coalesce(
        func.cast(CatalogModel.rating_sum, Numeric(6, 2))
        / func.nullif(CatalogModel.rating_count, 0),
        0,
    )


def _volume() -> ColumnElement[Any]:
    """The asset's volume, read correlated.

    Geometry has exactly one home (`ModelAsset`), so sorting reaches for it rather
    than reading a copy kept on the catalogue row that could disagree with the
    mesh it names.
    """
    return (
        select(ModelAsset.volume_cm3)
        .where(ModelAsset.id == CatalogModel.model_asset_id)
        .scalar_subquery()
    )


#: What each key orders on.
#:
#: Callables rather than expressions, so building one is deferred to query time
#: and the module imports without constructing eight SQL fragments nobody asked
#: for.
#:
#: The return type is `Any` rather than `ColumnElement[Any]` because a mapped
#: attribute (`CatalogModel.difficulty`) and a Core expression (`func.coalesce(…)`)
#: do not share a base that mypy can see here, though both are orderable at
#: runtime. Narrowing it would mean casting five of the eight entries, which buys
#: nothing: the only thing done with the result is `.asc()` / `.desc()`, one line
#: below, where a mistake is immediate and local.
_SORT_EXPRESSIONS: dict[SortKey, Callable[[], Any]] = {
    # Popularity is a *stated formula*, not an opaque score: prints weighted ten
    # to one against mean rating. This system shows the basis of every number it
    # prints, and a mystery integer between 0 and 100 would be the one figure on
    # the screen nobody could account for.
    SortKey.POPULAR: lambda: CatalogModel.print_count * 10 + _mean_rating(),
    SortKey.RATING: _mean_rating,
    SortKey.PRICE: lambda: CatalogModel.last_price,
    SortKey.TIME: lambda: CatalogModel.last_print_minutes,
    SortKey.VOLUME: _volume,
    SortKey.DIFFICULTY: lambda: CatalogModel.difficulty,
    SortKey.PRINTS: lambda: CatalogModel.print_count,
    SortKey.DATE: lambda: CatalogModel.published_at,
}


def _apply_sort(
    query: Select[tuple[CatalogModel]], sort: SortKey, descending: bool | None
) -> Select[tuple[CatalogModel]]:
    """Order by one of the eight keys.

    `descending=None` means "however this key opens" — see
    `DESCENDING_BY_DEFAULT`. An explicit value is the reader having clicked the
    active key to flip it.
    """
    if descending is None:
        descending = sort in DESCENDING_BY_DEFAULT

    expression = _SORT_EXPRESSIONS[sort]()
    ordered = expression.desc() if descending else expression.asc()
    # `id` breaks ties, so paging is stable. Without it two models with the same
    # rating can swap places between page one and page two and the reader sees one
    # twice and the other never — the classic unstable-pagination bug.
    return query.order_by(ordered, CatalogModel.id.asc())
