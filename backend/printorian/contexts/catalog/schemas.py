"""Views and inputs for the model library and the plate library."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from printorian.contexts.catalog.models import ModelFormat, PlateStatus
from printorian.core.ids import EntityId


class ModelAssetView(BaseModel):
    """An uploaded mesh as everything downstream sees it.

    `sha256` is the field that matters most: it is what `plate_key` consumes, so a
    client that keeps it can ask "has this been sliced before?" without re-uploading
    anything.
    """

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    sha256: str
    original_filename: str = ""
    format: ModelFormat = ModelFormat.STL
    size_bytes: int = 0

    triangle_count: int = 0
    volume_cm3: Decimal = Decimal(0)
    width_mm: Decimal = Decimal(0)
    depth_mm: Decimal = Decimal(0)
    height_mm: Decimal = Decimal(0)
    is_watertight: bool = False
    #: The full analysis, warnings included. `measured` is false for a format the
    #: farm stores but cannot read, so a client can say so rather than showing zeroes.
    mesh: dict[str, Any] = Field(default_factory=dict)

    uploaded_by: EntityId | None = None
    last_used_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def is_priceable(self) -> bool:
        return self.is_watertight and self.volume_cm3 > 0


class PreparedPlateView(BaseModel):
    """A cached plate as the prep queue and the dispatcher see it."""

    model_config = ConfigDict(from_attributes=True)

    id: EntityId
    plate_key: str
    status: PlateStatus

    model_hash: str = ""
    model_name: str = ""
    scale: Decimal = Decimal(1)
    material_code: str = ""
    printer_profile: str = ""
    layout_hash: str = ""

    #: How many copies of the model are on the plate, or `None` when nobody wrote
    #: it down. `None` is what stops the unattended intake path attaching it: the
    #: plate's minutes and grams are the *whole* bed's, and dividing them by an
    #: assumed layout is how a two-up plate priced itself as a one-up one.
    copies: int | None = None
    #: Exact, from the slicer — not the mesh heuristic the order was priced from.
    print_minutes: Decimal = Decimal(0)
    #: Grams per slot, as strings so no decimal precision is lost through JSON.
    filament_grams: dict[str, str] = Field(default_factory=dict)
    layer_count: int | None = None
    total_grams: Decimal = Decimal(0)

    filename: str = ""
    content_sha256: str | None = None
    storage_path: str | None = None
    size_bytes: int | None = None
    model_asset_id: EntityId | None = None
    #: Whether there are bytes to send to a printer, as opposed to only numbers.
    has_content: bool = False

    sliced_by: EntityId | None = None
    sliced_at: datetime | None = None
    slicer_name: str = ""
    slicer_version: str = ""
    profile_version: str = ""


class RecordPlate(BaseModel):
    """What an engineer's slicing produced."""

    model_hash: str = Field(min_length=1, max_length=64)
    model_name: str = Field(default="", max_length=300)
    scale: Decimal = Decimal(1)
    material_code: str = Field(min_length=1, max_length=80)
    printer_profile: str = Field(min_length=1, max_length=120)
    layout_hash: str = Field(default="", max_length=64)

    #: How many copies of the model this slice put on the bed.
    #:
    #: Optional, and **not** defaulted to one: an engineer who does not say leaves
    #: the plate usable by every path where a person can look at the bed, and
    #: unusable by the unattended one (`workers/cached_plates.py`). Defaulting it
    #: would be the automatic path acting on a layout nobody recorded.
    #:
    #: Not parsed from the 3MF either, though the container is already read for
    #: minutes and grams. `plate_file.py` says in its own docstring that the sliced
    #: `<plate>` shape is implemented from documentation and has never been seen
    #: from this farm's slicer — and a *miscount* here does not fail loudly, it
    #: attaches the wrong plate quietly. The number is asked for instead.
    copies: int | None = Field(default=None, ge=1)
    print_minutes: Decimal = Field(ge=0)
    #: Per AMS slot. A total would hide that one spool is nearly out.
    filament_grams: dict[str, Decimal] = Field(default_factory=dict)
    layer_count: int | None = None

    filename: str = Field(default="", max_length=300)
    #: Content address of the plate file. Null when the numbers were typed in and
    #: no file was uploaded — a legitimate state, and one the dispatcher must be
    #: able to tell from a plate it can actually send.
    content_sha256: str | None = Field(default=None, max_length=64)
    storage_path: str | None = None
    size_bytes: int | None = None
    #: The stored mesh this was sliced from, where the farm still holds it.
    model_asset_id: EntityId | None = None

    #: Provenance is required by ADR-0006 — a plate that cannot say which slicer
    #: and profile produced it cannot be confidently invalidated later.
    sliced_by: EntityId | None = None
    slicer_name: str = Field(default="", max_length=80)
    slicer_version: str = Field(default="", max_length=40)
    profile_version: str = Field(default="", max_length=40)
