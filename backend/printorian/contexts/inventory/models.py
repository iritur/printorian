"""Persistent models for materials.

The split V1 did not make (see docs/GLOSSARY.md): a **spec** is a catalogue
identity with prices and properties; a **lot** is a physical spool with a mass and
a location. V1's single `Spool` entity conflated them, which is why its materials
table could not answer "how much PLA Black do we have, and where".

The *where* half is `StorageZone` and `StorageCell` below: a named area of the
warehouse, and one addressable place inside it. They live here rather than in a
context of their own because "a lot is a physical spool with a mass and a place"
is the responsibility this module already names — a cell without a lot in it is
of no interest to anything.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.inventory.policies import LocationKind
from printorian.core.db import Entity, enum_column
from printorian.core.ids import EntityId


class MaterialSpec(Entity):
    """A catalogue material: PLA Matte Black, its properties and its prices."""

    __tablename__ = "material_specs"
    __table_args__ = (
        Index("ix_material_specs_family_active", "family", "is_active"),
        CheckConstraint("density_g_per_cm3 > 0", name="density_positive"),
        CheckConstraint("sell_price_per_gram >= 0", name="sell_price_non_negative"),
        CheckConstraint(
            "purchase_price_per_1000m IS NULL OR purchase_price_per_1000m >= 0",
            name="purchase_price_non_negative",
        ),
    )

    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    family: Mapped[str] = mapped_column(String(40), nullable=False)
    form: Mapped[str] = mapped_column(String(20), nullable=False, default="filament")
    color_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    color_hex: Mapped[str] = mapped_column(String(9), nullable=False, default="#888888")

    density_g_per_cm3: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("1.24")
    )
    #: What the farm pays, per the scenario's "last price to buy per 1000 m".
    purchase_price_per_1000m: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: What the customer pays. The pricing engine consumes this.
    sell_price_per_gram: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)

    # -- properties used by usage-scenario matching (seeded from the F3DP data)
    tensile_mpa: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    hdt_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    is_flexible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_outdoor_safe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nozzle_temp_range: Mapped[str | None] = mapped_column(String(40), nullable=True)
    bed_temp_range: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Open purchase orders are modelled in Phase 6; until then this carries the
    #: "ordered" status the scenario's table needs.
    has_open_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    lots: Mapped[list[MaterialLot]] = relationship(
        back_populates="spec", cascade="all, delete-orphan"
    )


class StorageZone(Entity):
    """A named area of the warehouse — «Зона A», the dry cabinet, the reject bin.

    ``temp_c`` and ``humidity_percent`` are the conditions the kit's zone header
    prints, and both are nullable **on purpose**: a zone nobody has hung a sensor
    in has not been measured, and 0 °C is a reading rather than an absence
    (ADR-0007). Defaulting either to zero would put a number on the screen the
    farm never took.

    They are the last reading, kept on the zone rather than in a series: this
    slice draws one figure per zone and a history nothing reads is a table to
    migrate later for no benefit. When drying state arrives it will want the
    series, and that is the point to move them.
    """

    __tablename__ = "storage_zones"
    __table_args__ = (
        CheckConstraint(
            "humidity_percent IS NULL OR (humidity_percent >= 0 AND humidity_percent <= 100)",
            name="humidity_is_a_percentage",
        ),
    )

    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    temp_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    humidity_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    cells: Mapped[list[StorageCell]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )


class StorageCell(Entity):
    """One addressable place — the ``A1-1`` a batch label carries.

    ``address`` is unique farm-wide rather than per zone, because it is what gets
    written on a spool and read back by somebody standing in the aisle. Scoping it
    to a zone would make ``A1-1`` ambiguous the moment a second zone reused it, and
    the person holding the spool has no zone in their hand.

    ``capacity_lots`` is **nullable and never defaulted**, and that is the ADR-0007
    decision in this file. A cell whose capacity nobody declared has no fill
    percentage — not 0%, which reads as empty, and not 100%, which is what a
    default of 1 would produce for every cell holding a single spool. The same
    shape `PreparedPlate.copies` took in migration 0023: the column says whether
    the farm has told us, and the read path answers ``None`` when it has not.
    """

    __tablename__ = "storage_cells"
    __table_args__ = (
        Index("ix_storage_cells_zone_id", "zone_id"),
        CheckConstraint(
            "capacity_lots IS NULL OR capacity_lots >= 1", name="capacity_positive_when_declared"
        ),
    )

    zone_id: Mapped[EntityId] = mapped_column(
        ForeignKey("storage_zones.id", ondelete="CASCADE"), nullable=False
    )
    #: The label an operator reads off the shelf. Short on purpose: `A1-1`, not a
    #: sentence — the kit prints it inside a `.hv-node` about forty pixels wide.
    address: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    capacity_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    zone: Mapped[StorageZone] = relationship(back_populates="cells")


class MaterialLot(Entity):
    """One physical spool or bottle, with a mass and a place."""

    __tablename__ = "material_lots"
    __table_args__ = (
        Index("ix_material_lots_spec_id_location_kind", "spec_id", "location_kind"),
        # "What is loaded in this machine" — the AMS view, and the scheduler's
        # material check. Previously unindexed and unconstrained.
        Index("ix_material_lots_printer_id", "printer_id"),
        CheckConstraint("initial_grams >= 0", name="initial_grams_non_negative"),
        CheckConstraint("remaining_grams >= 0", name="remaining_grams_non_negative"),
        # A spool cannot hold more than it started with. Consumption only subtracts,
        # so a value above the initial mass means an accounting bug, and the place to
        # catch that is before it reaches the scheduler as phantom filament.
        CheckConstraint("remaining_grams <= initial_grams", name="remaining_within_initial"),
    )

    spec_id: Mapped[EntityId] = mapped_column(
        ForeignKey("material_specs.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    initial_grams: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    remaining_grams: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    lot_number: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # -- location (see policies.Location)
    location_kind: Mapped[LocationKind] = mapped_column(
        enum_column(LocationKind), nullable=False, default=LocationKind.STOCK
    )
    #: The pre-cell free text, kept alive and alone. It is an unvalidated
    #: ``String(60)`` written by hand before `storage_cells` existed, so there is no
    #: honest mapping from it to an address and migration 0024 deliberately
    #: backfills nothing from it (ADR-0007). Which of the two answers wins when both
    #: are set is stated in exactly one place — `policies.Location` — so they cannot
    #: come to disagree the way `printer_id` and its old string spelling did.
    shelf: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Where the spool sits when it is not in a machine.
    #:
    #: ``SET NULL`` for the same reason `printer_id` below carries it: retiring a
    #: cell unplaces the spool, it does not destroy it. The movement ledger keeps
    #: the address as text, so the history of a retired cell survives this column
    #: going null — see `movements.MaterialMovement`.
    cell_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("storage_cells.id", ondelete="SET NULL"), nullable=True
    )
    #: A real reference to the machine, not a loose string.
    #:
    #: This was ``String(80)`` while `AmsSlot.printer_id` beside it was a UUID
    #: foreign key — two incompatible spellings of the same relationship, one of
    #: which the database could not check. Deleting a printer left orphaned text
    #: here and cascaded there, so "which machine holds this spool" had two answers
    #: that could disagree. ``SET NULL``: removing a machine unmounts its filament,
    #: it does not destroy the spool.
    printer_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )
    ams_unit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ams_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)

    spec: Mapped[MaterialSpec] = relationship(back_populates="lots")
    cell: Mapped[StorageCell | None] = relationship()

    @property
    def is_empty(self) -> bool:
        return self.remaining_grams <= 0

    @property
    def cell_address(self) -> str | None:
        """The address this spool sits at, or ``None`` — never a placeholder.

        Guarded on ``cell_id`` before the relationship is touched, and that guard
        is load-bearing rather than an optimisation: most lots in the farm have no
        cell at all, and under asyncio a lazy load raises `MissingGreenlet` rather
        than returning anything. An unplaced lot must be answerable without a
        query, because every read path has one.
        """
        if self.cell_id is None:
            return None
        return self.cell.address if self.cell is not None else None
