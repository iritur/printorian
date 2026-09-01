"""What a customer uploaded, and what an engineer sliced from it.

Two tables and one idea: **the geometry is the identity, not the filename.**

`ModelAsset` is the uploaded mesh as a first-class record, content-addressed by
SHA-256. `PreparedPlate` is what slicing produced from one configuration of it,
keyed on that same digest — which is what makes ADR-0006 work: the first order of a
configuration goes through an engineer, every later one reuses the cached plate and
dispatches with no human action.

Neither holds bytes. Both name an object in the store (`core.storage`), because a
mesh is tens of megabytes and a database is the wrong place for it (ARCHITECTURE
§10).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from printorian.core.db import Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class ModelFormat(StrEnum):
    """What kind of file was uploaded."""

    STL = "stl"
    THREE_MF = "3mf"
    #: Stored and served, but not measured. Pricing needs geometry, so an order
    #: cannot be placed against one of these until a parser for it exists.
    OTHER = "other"


class ModelAsset(Entity):
    """One uploaded mesh, stored once however many times it is sent.

    **Why a table and not just a folder.** The folder holds the bytes; this holds
    the answers the folder cannot give:

    * *which* file an order line was priced from — `OrderLine.model_name` is a
      display string, and two customers uploading different geometry as `part.stl`
      are otherwise indistinguishable;
    * the digest that `plate_key` is built on, without which the plate cache can
      never hit and every repeat order goes back through an engineer;
    * the mesh analysis, so the configurator stops re-parsing the same file on
      every option change;
    * whether anything still references the file, which is what retention needs and
      a modification time cannot answer.

    Content-addressed, so re-uploading a file the farm already holds costs one hash
    and no disk. That is not only a saving: it is what makes a catalogue model and a
    customer's own upload of the same part share a prepared plate.
    """

    __tablename__ = "model_assets"
    __table_args__ = (
        # The deduplication guarantee. Two uploads of identical geometry are one
        # asset, one stored object, and — through `plate_key` — one prepared plate.
        UniqueConstraint("sha256", name="uq_model_assets_sha256"),
        # Retention sweeps on this; without the index the cleanup is a full scan of
        # the table it is cleaning.
        Index("ix_model_assets_last_used_at", "last_used_at"),
        Index("ix_model_assets_uploaded_by", "uploaded_by"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint("triangle_count >= 0", name="triangle_count_non_negative"),
        CheckConstraint("volume_cm3 >= 0", name="volume_non_negative"),
    )

    #: The content address. Hex SHA-256 of the bytes as uploaded — the same value
    #: `plate_key` consumes as `model_hash`, and the object's name in the store.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: What the customer called it. Shown to people, never used to find anything.
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    format: Mapped[ModelFormat] = mapped_column(
        enum_column(ModelFormat, length=10), nullable=False, default=ModelFormat.STL
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Where the bytes are, relative to the store's root. Relative so that moving
    #: the storage directory — or restoring onto a differently-laid-out box — does
    #: not invalidate every row here.
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # -- geometry --------------------------------------------------------
    # The numbers a query might filter on are columns; the full analysis, warnings
    # and all, is kept whole beside them. "Which models fit a 256 mm bed" is a real
    # question and should not require reading JSON for every row.
    triangle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    volume_cm3: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal(0))
    width_mm: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    depth_mm: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    height_mm: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    #: A mesh with holes has no defined volume, so it cannot be priced. Recorded
    #: rather than rejected outright: the file is still worth keeping and showing.
    is_watertight: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The whole `MeshAnalysis`, warnings included, as measured at upload.
    mesh: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)

    # -- provenance and lifetime -----------------------------------------
    #: ``SET NULL``: a customer closing their account must not delete geometry that
    #: an open order still has to print.
    uploaded_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Bumped whenever the asset is quoted, ordered or prepared. Retention counts
    #: from here rather than from `created_at`, so a model the farm reprints every
    #: month is never collected while an experiment from last year is.
    last_used_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    @property
    def is_priceable(self) -> bool:
        """Whether an order may be placed against this geometry."""
        return self.is_watertight and self.volume_cm3 > 0


class PlateStatus(StrEnum):
    """Whether a cached plate may still be used."""

    VALID = "valid"
    #: The model or the profile changed underneath it. Kept rather than deleted so
    #: a job that already used it stays explicable.
    STALE = "stale"
    #: An engineer looked at it and said no.
    REJECTED = "rejected"


class PreparedPlate(Entity):
    """A sliced configuration, with the truth slicing produced."""

    __tablename__ = "prepared_plates"
    __table_args__ = (
        # The cache guarantee. Without it two engineers slicing the same
        # configuration at once produce two rows, and which one later orders hit
        # becomes a race.
        UniqueConstraint("plate_key", name="uq_prepared_plates_key"),
        Index("ix_prepared_plates_sliced_by", "sliced_by"),
        # "Which plates exist for this geometry, whatever they were sliced for?" —
        # the question `find_unambiguous` asks on **every line of every paid
        # order**, because intake has no printer profile to build the unique key
        # from. Without it that is a sequential scan of every plate the farm has
        # ever produced, thirty seconds apart, for ever.
        Index("ix_prepared_plates_model_hash", "model_hash"),
        # "Which plates were sliced from this model?" — the question asked when a
        # model is superseded and its plates have to be marked stale.
        Index("ix_prepared_plates_model_asset_id", "model_asset_id"),
        CheckConstraint("print_minutes >= 0", name="print_minutes_non_negative"),
        CheckConstraint("scale > 0", name="scale_positive"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_non_negative"),
    )

    #: Content-addressed over the ADR-0006 key tuple. See `plate_key.py`.
    #:
    #: No separate index: the unique constraint above already builds one, and a
    #: second index on the same column costs a write on every insert and serves no
    #: read the first cannot.
    plate_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PlateStatus] = mapped_column(
        enum_column(PlateStatus), nullable=False, default=PlateStatus.VALID
    )

    # -- the key's parts, kept readable ----------------------------------
    # Denormalised on purpose: a hash cannot be read by a human deciding whether a
    # plate should be invalidated, and "which plates use this profile?" is a
    # question the farm will ask.
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: The asset this was sliced from, where the farm still holds it.
    #:
    #: `model_hash` above remains the key's input and is never null — the cache is
    #: keyed on geometry, so a plate stays valid and findable even after retention
    #: has collected the source mesh. This is the convenience link for "show me the
    #: model behind this plate", and it goes null rather than taking the plate with
    #: it when the asset is gone.
    model_asset_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("model_assets.id", ondelete="SET NULL"), nullable=True
    )
    scale: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal(1))
    material_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    printer_profile: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    layout_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # -- what slicing actually found -------------------------------------
    #: Exact, from the slicer — not the mesh heuristic that priced the order.
    print_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    #: Grams per AMS slot, e.g. {"0": 12.4, "1": 3.1}. Per slot rather than a
    #: total because a multi-colour plate can exhaust one spool while others are
    #: full, and the scheduler needs to know which.
    filament_grams: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)
    layer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- the file --------------------------------------------------------
    filename: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: Content address of the plate itself, in the same store as the model. Null
    #: until an engineer has uploaded the sliced file — a plate row can exist with
    #: numbers typed in and no bytes yet, and the dispatcher must be able to tell
    #: the difference rather than sending an empty file to a printer.
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Where the bytes live, relative to the store root. Not the bytes: plates are
    #: tens of megabytes and a database is the wrong place for them.
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: ``BigInteger``: a 32-bit column caps at 2.1 GB, which is comfortable for a
    #: plate and not obviously comfortable for the model assets that will share this
    #: pattern. Widening later is a table rewrite; widening now is free.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # -- provenance ------------------------------------------------------
    # ADR-0006 requires this: a plate that cannot say who produced it, with which
    # slicer and which profile, cannot be invalidated with any confidence when one
    # of those changes.
    #: ``SET NULL``: an engineer leaving must not delete the plates they prepared,
    #: and a plate whose provenance is partly unknown is still a usable plate.
    sliced_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sliced_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    slicer_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    slicer_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    profile_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    @property
    def has_content(self) -> bool:
        """Whether there are bytes to send to a printer.

        A plate can be recorded from typed numbers before its file is uploaded.
        Dispatching one of those would put an empty file on a machine, so the
        dispatcher checks this rather than assuming a row implies a file.
        """
        return bool(self.content_sha256)

    @property
    def total_grams(self) -> Decimal:
        """Filament across every slot."""
        return sum(
            (Decimal(str(grams)) for grams in self.filament_grams.values()),
            Decimal(0),
        )

    @property
    def is_usable(self) -> bool:
        return self.status is PlateStatus.VALID
