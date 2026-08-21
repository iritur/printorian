"""Persistent models for the fleet: the machine, its slots, and its service card.

The access code is stored **encrypted** (ADR-0014) and is write-only across the API:
it can be set and replaced, never read back. A UI shows "set" or "not set".

Everything here is *live* state — the row a poll overwrites and a screen reads for
"now". The measured history it leaves behind, `telemetry_samples` and the hourly
`metric_rollups` over them, lives in :mod:`printorian.contexts.fleet.history`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.fleet.policies import ConnectionMode, MaintenanceKind
from printorian.core.db import Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId
from printorian.drivers import PrinterState


class Printer(Entity):
    """One machine in the farm."""

    __tablename__ = "printers"
    __table_args__ = (
        Index("ix_printers_state_is_active", "state", "is_active"),
        CheckConstraint("build_width_mm > 0", name="build_width_positive"),
        CheckConstraint("build_depth_mm > 0", name="build_depth_positive"),
        CheckConstraint("build_height_mm > 0", name="build_height_positive"),
        CheckConstraint("nozzle_diameter_mm > 0", name="nozzle_diameter_positive"),
        CheckConstraint("printed_hours >= 0", name="printed_hours_non_negative"),
        CheckConstraint("expected_lifetime_hours > 0", name="lifetime_positive"),
        CheckConstraint("nominal_power_kw >= 0", name="power_non_negative"),
        CheckConstraint("acquisition_cost >= 0", name="acquisition_cost_non_negative"),
    )

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    brand: Mapped[str] = mapped_column(String(40), nullable=False, default="bambu")
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    serial: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    connection_mode: Mapped[ConnectionMode] = mapped_column(
        enum_column(ConnectionMode), nullable=False, default=ConnectionMode.MANUAL
    )
    host: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Ciphertext only. Never returned by any endpoint (ADR-0014).
    access_code_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # -- capability ------------------------------------------------------
    build_width_mm: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal(256)
    )
    build_depth_mm: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal(256)
    )
    build_height_mm: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal(256)
    )
    nozzle_diameter_mm: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("0.4")
    )
    supports_multi_material: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # -- live state ------------------------------------------------------
    state: Mapped[PrinterState] = mapped_column(
        enum_column(PrinterState), nullable=False, default=PrinterState.OFFLINE
    )
    #: Last observation, as reported. Never synthesized (ADR-0007).
    last_telemetry: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    storage_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # -- economics -------------------------------------------------------
    acquisition_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal(0)
    )
    expected_lifetime_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=20_000)
    #: Cumulative printing hours, the basis for amortization and service intervals.
    printed_hours: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal(0)
    )
    nominal_power_kw: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False, default=Decimal("0.35")
    )

    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    slots: Mapped[list[AmsSlot]] = relationship(
        back_populates="printer",
        cascade="all, delete-orphan",
        order_by="AmsSlot.unit, AmsSlot.index",
    )
    services: Mapped[list[ServiceOperation]] = relationship(
        back_populates="printer", cascade="all, delete-orphan"
    )


class AmsSlot(Entity):
    """One material slot, and what is physically in it.

    This is the join the scenario's materials table needs — "in printer + AMS port"
    as a location — and the one the scheduler filters on.
    """

    __tablename__ = "ams_slots"
    __table_args__ = (
        # **Unique**, not merely indexed. A slot is a physical position: there is
        # one unit A slot 3 on a machine and there cannot be two. A retried
        # telemetry write or a reconnect that re-imports the AMS layout would
        # otherwise insert a second row, and the scheduler would then see — and
        # plan against — capacity that does not exist.
        UniqueConstraint("printer_id", "unit", "index", name="uq_ams_slots_printer_id_unit_index"),
        # "which slot holds this lot", and the index the lot's `SET NULL` delete
        # needs in order not to scan every slot on the farm.
        Index("ix_ams_slots_lot_id", "lot_id"),
        CheckConstraint("unit >= 0", name="unit_non_negative"),
        CheckConstraint('"index" >= 0', name="index_non_negative"),
        CheckConstraint(
            "remaining_percent IS NULL OR (remaining_percent BETWEEN 0 AND 100)",
            name="remaining_percent_range",
        ),
    )

    printer_id: Mapped[EntityId] = mapped_column(
        ForeignKey("printers.id", ondelete="CASCADE"), nullable=False
    )
    unit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    index: Mapped[int] = mapped_column(Integer, nullable=False)

    #: As reported by the machine. Null when the slot is empty.
    material_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    colour_hex: Mapped[str | None] = mapped_column(String(9), nullable=True)
    remaining_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The physical lot mounted here, when the farm has identified it.
    #:
    #: A real foreign key: this is the third corner of the material ↔ slot ↔ printer
    #: triangle the scheduler's hard eligibility filter runs on, and it was the one
    #: corner nothing checked. A consumed lot that was deleted left a dangling id
    #: here, and the planner read it as filament that is mounted and available.
    lot_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("material_lots.id", ondelete="SET NULL"), nullable=True
    )

    printer: Mapped[Printer] = relationship(back_populates="slots")


class ServiceOperation(Entity):
    """An entry on the printer's service card (scenario item M3).

    Carries its own periodicity, so "what is due on this machine" is answerable
    without a separate schedule table that could drift out of step.
    """

    __tablename__ = "service_operations"
    __table_args__ = (
        Index("ix_service_operations_printer_id", "printer_id"),
        CheckConstraint("interval_hours > 0", name="interval_positive"),
        CheckConstraint("last_done_at_hours >= 0", name="last_done_hours_non_negative"),
    )

    printer_id: Mapped[EntityId] = mapped_column(
        ForeignKey("printers.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[MaintenanceKind] = mapped_column(enum_column(MaintenanceKind), nullable=False)
    #: Printing hours between services. Wear follows use, not the calendar.
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    last_done_at_hours: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal(0)
    )
    last_done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Consumables the operation uses, e.g. ``["nozzle-0.4", "ipa"]``.
    materials_used: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    printer: Mapped[Printer] = relationship(back_populates="services")

    def is_due(self, printed_hours: Decimal) -> bool:
        return printed_hours - self.last_done_at_hours >= Decimal(self.interval_hours)
