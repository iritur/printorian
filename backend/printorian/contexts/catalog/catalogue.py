"""The model library — what a customer can order without uploading anything.

Separate from :mod:`models`, which is about *a* mesh: `ModelAsset` is geometry the
farm holds, `PreparedPlate` is what slicing produced from it. A `CatalogModel` is
an editorial decision — this part is worth offering, in these materials, at this
difficulty — layered on top of geometry that already exists.

**The claim the catalogue makes is that its numbers are measured.** Time and price
come from the last real print, not from volume × coefficient. That is the whole
argument of the screen, so the measured fields are nullable and stay null until a
job has actually succeeded: a model nobody has printed says so, and the caller
falls back to an estimate *labelled as an estimate*. Filling them with a
prediction would be ADR-0007's defect — a driver inventing data — moved into the
catalogue.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.catalog.models import ModelAsset
from printorian.core.db import Base, Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class ModelCategory(StrEnum):
    """The kit's five facet values, and the shelf a model sits on."""

    FUNCTIONAL = "func"
    CASE = "case"
    MECHANICAL = "mech"
    ORGANISER = "org"
    DECOR = "decor"


class Suitability(StrEnum):
    """How well a material suits a model, as the kit's «Пригодность» column.

    Editorial, like the spec bars: nothing in the geometry says whether a part
    will survive outdoors. The three grades map onto the kit's state tones —
    `idle` for excellent, `preparing` for good, `paused` for limited — which is
    the same vocabulary printers use, deliberately: a material that is merely
    workable reads the way a machine that is merely idle does.
    """

    EXCELLENT = "excellent"
    GOOD = "good"
    LIMITED = "limited"


class SizeClass(StrEnum):
    """Small / medium / large, as the catalogue's size facet."""

    SMALL = "s"
    MEDIUM = "m"
    LARGE = "l"


#: Longest-edge thresholds, in millimetres.
#:
#: A policy rather than a magic number in a query: "large" has to mean the same
#: thing in the facet, in the card and in a conversation about the shelf. Keyed on
#: the longest edge because that is what decides whether a part fits a plate — a
#: long thin bracket is a big print even though its volume is small.
#:
#: The numbers come from the kit, whose facet *states* them — «До 50 мм»,
#: «50–150 мм», «Более 150 мм». A label that names a threshold has to be true,
#: so the policy follows the label rather than the other way round.
SMALL_MAX_MM = Decimal(50)
MEDIUM_MAX_MM = Decimal(150)


def size_class_of(width_mm: Decimal, depth_mm: Decimal, height_mm: Decimal) -> SizeClass:
    """Which shelf a bounding box belongs on.

    Pure, so the same rule can be applied at write time, asserted in a test, and
    explained to somebody asking why their part is "medium".
    """
    longest = max(width_mm, depth_mm, height_mm)
    if longest <= SMALL_MAX_MM:
        return SizeClass.SMALL
    if longest <= MEDIUM_MAX_MM:
        return SizeClass.MEDIUM
    return SizeClass.LARGE


class CatalogModel(Entity):
    """One offered model.

    Points at a `ModelAsset` rather than restating its geometry: volume, bounding
    box and triangle count have exactly one home, and a catalogue entry that
    disagreed with the mesh it names would be a lie the screen could not detect.
    """

    __tablename__ = "catalog_models"
    __table_args__ = (
        # The public identifier. A slug rather than the id, because it appears in
        # a URL a customer may share and `MDL-0412` is not a thing anyone types.
        UniqueConstraint("slug", name="uq_catalog_models_slug"),
        # The catalogue's own default ordering, and the facet columns it filters
        # on. One composite index rather than four single ones: every query this
        # screen makes filters on `is_published` first.
        Index("ix_catalog_models_published", "is_published", "category", "size_class"),
        Index("ix_catalog_models_asset", "model_asset_id"),
        CheckConstraint("difficulty BETWEEN 0 AND 10", name="difficulty_range"),
        CheckConstraint("strength BETWEEN 0 AND 10", name="strength_range"),
        CheckConstraint("accuracy BETWEEN 0 AND 10", name="accuracy_range"),
        CheckConstraint("speed BETWEEN 0 AND 10", name="speed_range"),
        CheckConstraint("supports BETWEEN 0 AND 10", name="supports_range"),
        CheckConstraint("postprocessing BETWEEN 0 AND 10", name="postprocessing_range"),
        CheckConstraint("print_count >= 0", name="print_count_non_negative"),
        CheckConstraint("rating_count >= 0", name="rating_count_non_negative"),
        CheckConstraint("rating_sum >= 0", name="rating_sum_non_negative"),
        CheckConstraint(
            "last_print_minutes IS NULL OR last_print_minutes >= 0",
            name="last_print_minutes_non_negative",
        ),
        CheckConstraint(
            "last_print_grams IS NULL OR last_print_grams >= 0",
            name="last_print_grams_non_negative",
        ),
        CheckConstraint("last_price IS NULL OR last_price >= 0", name="last_price_non_negative"),
    )

    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The short uppercase code the kit prints on every card — `BRACKET_V4`.
    code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False, default="")

    #: Title, code and tags, folded to lower case **in Python**, for the search box.
    #:
    #: A derived column rather than `lower(title) LIKE ...` because SQL case
    #: folding is not portable over Cyrillic: SQLite's `lower()` is ASCII-only, so
    #: `Кронштейн` never matches `кронштейн` there, while PostgreSQL folds it
    #: correctly. A catalogue whose entire vocabulary is Russian cannot rest on a
    #: function that works in production and silently fails in the test suite —
    #: that is a green build for a broken search box.
    #:
    #: Maintained by the mapper event below, so it cannot drift from the fields it
    #: is derived from.
    search_text: Mapped[str] = mapped_column(String(2400), nullable=False, default="")

    category: Mapped[ModelCategory] = mapped_column(enum_column(ModelCategory), nullable=False)
    #: Derived from the asset's bounding box by `size_class_of`, then stored so the
    #: facet is an indexed equality rather than arithmetic over three columns on
    #: every row of every query.
    size_class: Mapped[SizeClass] = mapped_column(enum_column(SizeClass), nullable=False)
    #: 0–10, as the kit's "ОЦЕНКА 0–10" spec bars. Editorial, not computed.
    #:
    #: All six are a judgement somebody made about the part, which is why they are
    #: stored rather than derived: nothing in the geometry says whether a model
    #: needs supports, and a number computed from volume would be a guess wearing a
    #: measurement's clothes. They default to zero, and zero reads as "not yet
    #: assessed" rather than "worst possible" — the screen shows the bar empty.
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strength: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accuracy: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    speed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supports: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    postprocessing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Who drew it. A display string, not a user reference: most catalogue models
    #: come from outside the farm, and the ones that do not are credited to a team
    #: rather than to whichever engineer happened to upload the file.
    author: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Whether the model is *designed* for more than one colour. Not "can it be
    #: printed in one" — everything can.
    multicolor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Free tags. Displayed and searched, never faceted — which is why they can
    #: stay a JSON list instead of earning a table like materials did.
    tags: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)

    model_asset_id: Mapped[EntityId] = mapped_column(
        ForeignKey("model_assets.id", ondelete="RESTRICT"), nullable=False
    )

    license: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    # -- accumulated, never editorial ------------------------------------
    #: Ratings are stored as sum and count rather than as an average, so a new
    #: rating is an increment instead of a read-modify-write that two reviewers
    #: can race on.
    rating_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    print_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # -- the measured claim ----------------------------------------------
    # All nullable, all null until a real job has succeeded. See the module
    # docstring: null here is the signal that makes the screen say "estimate".
    last_printed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Wall-clock minutes the machine actually took, not the slicer's prediction.
    last_print_minutes: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    last_print_grams: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    #: What one unit actually cost to quote, from the pinned breakdown of the order
    #: that print belonged to. A *record of a price that was charged*, never a
    #: recomputation: repricing here would drift from the snapshot the customer
    #: agreed to, which is the whole point of pinning it (ADR-0002).
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: Display name, not a foreign key: this is a historical fact about a print
    #: that happened, and it must survive the machine being decommissioned.
    last_printer_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: The card's line drawing, as SVG path data. Stored rather than rendered from
    #: the mesh because the kit's previews are deliberately *schematic* — an
    #: engineering drawing is honest about a part that does not exist yet, and it
    #: survives both themes where a render would not.
    preview: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)

    materials: Mapped[list[CatalogModelMaterial]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    #: The geometry this entry is about.
    #:
    #: `selectin` rather than lazy: every card prints the volume and the bounding
    #: box, so a grid of twenty-four would otherwise issue twenty-four extra
    #: queries — the N+1 that turns a fast page into a slow one at exactly the
    #: scale where nobody notices in development.
    asset: Mapped[ModelAsset] = relationship(lazy="selectin")

    @property
    def rating(self) -> Decimal:
        """Mean rating, or zero when nobody has rated it.

        Zero rather than `None`: the catalogue sorts on this, and a null in a sort
        key is a dialect-dependent surprise. "No rating" is carried by
        `rating_count`, which is the field that can say it without ambiguity.
        """
        if self.rating_count == 0:
            return Decimal(0)
        return (Decimal(self.rating_sum) / Decimal(self.rating_count)).quantize(Decimal("0.1"))

    @property
    def has_measured_print(self) -> bool:
        """Whether this model's headline numbers are facts rather than estimates."""
        return self.last_printed_at is not None and self.last_print_minutes is not None


def search_text_of(title: str, code: str, tags: list[str] | None) -> str:
    """What the search box matches against.

    `str.lower()` rather than SQL's, because Python's is Unicode-aware everywhere
    and SQL's is not — see `CatalogModel.search_text`. Pure, so the same rule can
    be applied on write, asserted in a test, and reasoned about without a database.
    """
    return " ".join([title, code, *(tags or [])]).lower()


@event.listens_for(CatalogModel, "before_insert")
@event.listens_for(CatalogModel, "before_update")
def _refresh_search_text(_mapper: object, _connection: object, target: CatalogModel) -> None:
    """Keep `search_text` in step with the fields it is derived from.

    A mapper event rather than a call at each write site: this is a cache of three
    other columns, and a cache that a caller can forget to refresh is a search box
    that quietly stops finding a model somebody renamed.
    """
    target.search_text = search_text_of(target.title, target.code, target.tags)


class CatalogModelMaterial(Base):
    """One material a model is offered in.

    A table rather than a JSON list because this is the one multi-valued field the
    catalogue *filters* on, and the facet is OR-within-group — "PLA or PETG". As
    JSON that is a containment operator, which exists in PostgreSQL and not in the
    SQLite the tests run on; as a row it is a join that behaves identically on
    both, which is what keeps the test suite meaningful.
    """

    __tablename__ = "catalog_model_materials"
    __table_args__ = (
        # "Which models are offered in PETG?" — the facet's own question, asked
        # from the material side.
        Index("ix_catalog_model_materials_code", "material_code"),
    )

    model_id: Mapped[EntityId] = mapped_column(
        ForeignKey("catalog_models.id", ondelete="CASCADE"), primary_key=True
    )
    #: The `MaterialSpec` code. Deliberately not a foreign key: the catalogue says
    #: what a model is *suitable for*, which stays true while the shop is out of
    #: stock and after a spec is retired.
    material_code: Mapped[str] = mapped_column(String(80), primary_key=True)

    suitability: Mapped[Suitability] = mapped_column(
        enum_column(Suitability), nullable=False, default=Suitability.GOOD
    )
    #: A caveat shown instead of the grade — the kit's «Не для улицы».
    #:
    #: One short phrase, not prose: the column is narrow and the reader is
    #: comparing four rows. The grade still drives the tone, so a caveat is
    #: coloured by how limiting it is rather than by its wording.
    note: Mapped[str] = mapped_column(String(60), nullable=False, default="")

    #: The baseline. Every other row's Δ price is measured against this one, and
    #: the kit marks it «· РЕКОМЕНДОВАН».
    #:
    #: Not constrained to one per model by the database: a half-edited row with
    #: two recommendations should render oddly, not refuse to save. The read side
    #: takes the first.
    is_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    model: Mapped[CatalogModel] = relationship(back_populates="materials")
