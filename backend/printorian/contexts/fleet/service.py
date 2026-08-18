"""Fleet use cases: register machines, record what they report, keep them serviced."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.fleet import events as fleet_events
from printorian.contexts.fleet.models import AmsSlot, Printer, ServiceOperation
from printorian.contexts.fleet.policies import (
    ConnectionMode,
    PrinterCapability,
    amortization_per_hour,
    needs_attention,
)
from printorian.contexts.fleet.samples import sample_of
from printorian.contexts.fleet.schemas import (
    AmsSlotView,
    CreatePrinter,
    CreateServiceOperation,
    MountLot,
    PrinterTable,
    PrinterView,
    ServiceOperationView,
    StatusCount,
)
from printorian.core.clock import Clock
from printorian.core.errors import ConflictError, NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.secrets import SecretBox, is_set
from printorian.drivers import ConnectionInfo, PrinterState, Telemetry
from printorian.drivers import ConnectionMode as DriverConnectionMode

_MINUTES_PER_HOUR = Decimal(60)


class FleetService:
    """The farm's machines, and the truth about what they are doing."""

    def __init__(
        self, session: AsyncSession, clock: Clock, bus: EventBus, secrets: SecretBox
    ) -> None:
        self._db = session
        self._clock = clock
        self._bus = bus
        self._secrets = secrets

    # -- registration ----------------------------------------------------

    async def register(self, data: CreatePrinter) -> PrinterView:
        existing = await self._db.scalar(select(Printer).where(Printer.name == data.name))
        if existing is not None:
            raise ConflictError("error.fleet.name_taken", name=data.name)

        fields = data.model_dump(exclude={"access_code"})
        printer = Printer(**fields)
        if data.access_code:
            printer.access_code_encrypted = self._secrets.encrypt(data.access_code)

        self._db.add(printer)
        await self._db.flush()
        await self._bus.publish(
            fleet_events.PrinterRegistered(printer_id=printer.id, printer_name=printer.name)
        )
        return await self.get(printer.id)

    async def set_access_code(self, printer_id: EntityId, access_code: str) -> PrinterView:
        """Store a new credential. The old one is overwritten, never archived."""
        printer = await self._db.get(Printer, printer_id)
        if printer is None:
            raise NotFoundError("error.fleet.not_found", printer_id=str(printer_id))

        printer.access_code_encrypted = self._secrets.encrypt(access_code)
        await self._db.flush()
        return await self.get(printer_id)

    def connection_for(self, printer: Printer) -> ConnectionInfo:
        """Build driver credentials, decrypting only at the moment of use.

        The plaintext exists here and nowhere else — not in a view, not in a log,
        not in an event payload.
        """
        code = (
            self._secrets.decrypt(printer.access_code_encrypted)
            if printer.access_code_encrypted
            else None
        )
        return ConnectionInfo(
            printer_id=str(printer.id),
            mode=_driver_mode(printer.connection_mode),
            host=printer.host,
            serial=printer.serial or None,
            access_code=code,
        )

    # -- reading ---------------------------------------------------------

    async def get(self, printer_id: EntityId) -> PrinterView:
        printer = await self._load(printer_id)
        return self._to_view(printer)

    async def table(self, *, include_inactive: bool = False) -> PrinterTable:
        """Rows plus state counts, for the scenario's printers screen."""
        query = (
            select(Printer)
            .options(selectinload(Printer.slots), selectinload(Printer.services))
            .execution_options(populate_existing=True)
            .order_by(Printer.name)
        )
        if not include_inactive:
            query = query.where(Printer.is_active.is_(True))

        rows = [self._to_view(printer) for printer in await self._db.scalars(query)]
        tally = Counter(row.state for row in rows)
        # Every state gets a chip, including the empty ones: "error 0" is
        # information, a missing chip is a gap.
        counts = [StatusCount(state=state, count=tally.get(state, 0)) for state in PrinterState]

        return PrinterTable(
            rows=rows,
            counts=counts,
            total=len(rows),
            attention=sum(1 for row in rows if row.needs_attention),
        )

    async def capabilities(self) -> list[PrinterCapability]:
        """What the scheduler filters on. Phase 4 consumes this."""
        query = (
            select(Printer)
            .options(selectinload(Printer.slots), selectinload(Printer.services))
            .execution_options(populate_existing=True)
            .where(Printer.is_active.is_(True))
        )
        return [self._to_capability(printer) for printer in await self._db.scalars(query)]

    # -- telemetry -------------------------------------------------------

    async def record(self, printer_id: EntityId, telemetry: Telemetry) -> PrinterView:
        """Persist an observation and publish the change.

        Cumulative printing hours advance from the gap between observations rather
        than from a job's estimate, so the amortization figure reflects the machine
        that actually ran, not the one that was planned.

        The observation is written **twice, on purpose**: onto the printer as
        `last_telemetry`, which every live view reads and every poll overwrites, and
        as a row in `telemetry_samples`, which nothing overwrites. The first answers
        "what is this machine doing"; only the second can answer "what did it do
        last Tuesday", which is what the dashboard's schedule and phase 6's measured
        electricity are both built on.
        """
        printer = await self._load(printer_id)
        previous = printer.state
        now = self._clock.now()

        if printer.state is PrinterState.PRINTING and printer.last_seen_at is not None:
            elapsed = Decimal((now - printer.last_seen_at).total_seconds()) / Decimal(3600)
            printer.printed_hours += max(Decimal(0), elapsed)

        printer.state = telemetry.state
        printer.last_seen_at = telemetry.observed_at
        printer.last_telemetry = {
            "progress_percent": telemetry.progress_percent,
            "layer_current": telemetry.layer_current,
            "layer_total": telemetry.layer_total,
            "remaining_minutes": (
                int(telemetry.remaining.minutes) if telemetry.remaining else None
            ),
            "job_handle": telemetry.job_handle,
            "error_code": telemetry.error_code,
        }
        self._sync_slots(printer, telemetry)
        self._db.add(sample_of(printer.id, telemetry))
        await self._db.flush()

        if previous is not telemetry.state:
            await self._bus.publish(
                fleet_events.PrinterStateChanged(
                    printer_id=printer.id,
                    printer_name=printer.name,
                    from_state=previous.value,
                    to_state=telemetry.state.value,
                )
            )
        return await self.get(printer_id)

    async def mark_unreachable(self, printer_id: EntityId, reason: str) -> PrinterView:
        """Record that a machine could not be reached.

        Offline is a *recorded observation*, not an absence of one. V1's connector
        answered this case with invented data; here the state changes and an
        attention event fires.
        """
        printer = await self._load(printer_id)
        previous = printer.state
        printer.state = PrinterState.OFFLINE
        await self._db.flush()

        if previous is not PrinterState.OFFLINE:
            await self._bus.publish(
                fleet_events.PrinterUnreachable(
                    printer_id=printer.id, printer_name=printer.name, reason=reason
                )
            )
        return await self.get(printer_id)

    def _sync_slots(self, printer: Printer, telemetry: Telemetry) -> None:
        """Mirror the machine's AMS state onto our slot rows."""
        existing = {(slot.unit, slot.index): slot for slot in printer.slots}
        for reported in telemetry.ams_slots:
            slot = existing.get((reported.unit, reported.index))
            if slot is None:
                slot = AmsSlot(unit=reported.unit, index=reported.index)
                printer.slots.append(slot)
            slot.material_type = reported.material_type
            slot.colour_hex = reported.colour_hex
            slot.remaining_percent = reported.remaining_percent

    # -- materials -------------------------------------------------------

    async def mount_lot(self, printer_id: EntityId, data: MountLot) -> PrinterView:
        """Record which physical lot is in which slot."""
        printer = await self._load(printer_id)
        slot = next(
            (s for s in printer.slots if s.unit == data.unit and s.index == data.index), None
        )
        if slot is None:
            slot = AmsSlot(unit=data.unit, index=data.index)
            printer.slots.append(slot)

        slot.lot_id = data.lot_id
        await self._db.flush()
        return await self.get(printer_id)

    async def clear_slot(self, printer_id: EntityId, *, unit: int, index: int) -> EntityId | None:
        """Empty an AMS slot, returning whichever lot was in it.

        The caller needs the lot id to move it in inventory, and only this side
        knows it — so it is returned rather than looked up twice. ``None`` means
        the slot was already empty, which is not an error: an operator recording
        a spool they removed earlier should not be refused.
        """
        printer = await self._load(printer_id)
        slot = next((s for s in printer.slots if s.unit == unit and s.index == index), None)
        if slot is None:
            return None

        previous = slot.lot_id
        slot.lot_id = None
        await self._db.flush()
        return previous

    # -- service card ----------------------------------------------------

    async def add_service_operation(
        self, printer_id: EntityId, data: CreateServiceOperation
    ) -> PrinterView:
        printer = await self._load(printer_id)
        self._db.add(
            ServiceOperation(
                printer_id=printer.id,
                kind=data.kind,
                interval_hours=data.interval_hours,
                last_done_at_hours=printer.printed_hours,
                materials_used=list(data.materials_used),
                notes=data.notes,
            )
        )
        await self._db.flush()
        return await self.get(printer_id)

    async def complete_service(self, printer_id: EntityId, operation_id: EntityId) -> PrinterView:
        """Reset an operation's clock to the machine's current printing hours."""
        operation = await self._db.get(ServiceOperation, operation_id)
        if operation is None or operation.printer_id != printer_id:
            raise NotFoundError("error.fleet.service_not_found", operation_id=str(operation_id))

        printer = await self._load(printer_id)
        operation.last_done_at_hours = printer.printed_hours
        operation.last_done_at = self._clock.now()
        await self._db.flush()
        return await self.get(printer_id)

    # -- internals -------------------------------------------------------

    async def _load(self, printer_id: EntityId) -> Printer:
        printer = await self._db.scalar(
            select(Printer)
            .options(selectinload(Printer.slots), selectinload(Printer.services))
            .execution_options(populate_existing=True)
            .where(Printer.id == printer_id)
        )
        if printer is None:
            raise NotFoundError("error.fleet.not_found", printer_id=str(printer_id))
        return printer

    def _to_view(self, printer: Printer) -> PrinterView:
        telemetry = printer.last_telemetry or {}
        remaining = telemetry.get("remaining_minutes")
        due = [op for op in printer.services if op.is_due(printer.printed_hours)]

        return PrinterView(
            id=printer.id,
            name=printer.name,
            brand=printer.brand,
            model=printer.model,
            serial=printer.serial,
            connection_mode=printer.connection_mode,
            host=printer.host,
            access_code_set=is_set(printer.access_code_encrypted),
            state=printer.state,
            last_seen_at=printer.last_seen_at,
            storage_available=printer.storage_available,
            progress_percent=telemetry.get("progress_percent"),
            remaining_minutes=remaining,
            eta=(
                self._clock.now() + timedelta(minutes=int(remaining))
                if remaining and printer.state is PrinterState.PRINTING
                else None
            ),
            current_job=telemetry.get("job_handle"),
            build_width_mm=printer.build_width_mm,
            build_depth_mm=printer.build_depth_mm,
            build_height_mm=printer.build_height_mm,
            nozzle_diameter_mm=printer.nozzle_diameter_mm,
            supports_multi_material=printer.supports_multi_material,
            printed_hours=printer.printed_hours,
            amortization_per_hour=amortization_per_hour(
                printer.acquisition_cost, printer.expected_lifetime_hours
            ),
            nominal_power_kw=printer.nominal_power_kw,
            location=printer.location,
            is_active=printer.is_active,
            maintenance_due=bool(due),
            needs_attention=needs_attention(printer.state, maintenance_due=bool(due)),
            slots=[AmsSlotView.model_validate(slot) for slot in printer.slots],
            services=[
                ServiceOperationView(
                    id=op.id,
                    kind=op.kind,
                    interval_hours=op.interval_hours,
                    last_done_at_hours=op.last_done_at_hours,
                    last_done_at=op.last_done_at,
                    materials_used=list(op.materials_used),
                    notes=op.notes,
                    is_due=op.is_due(printer.printed_hours),
                    hours_until_due=max(
                        Decimal(0),
                        op.last_done_at_hours + Decimal(op.interval_hours) - printer.printed_hours,
                    ),
                )
                for op in printer.services
            ],
        )

    @staticmethod
    def _to_capability(printer: Printer) -> PrinterCapability:
        due = any(op.is_due(printer.printed_hours) for op in printer.services)
        return PrinterCapability(
            printer_id=str(printer.id),
            state=printer.state,
            width_mm=printer.build_width_mm,
            depth_mm=printer.build_depth_mm,
            height_mm=printer.build_height_mm,
            nozzle_diameter_mm=printer.nozzle_diameter_mm,
            supports_multi_material=printer.supports_multi_material,
            loaded=tuple(
                (
                    slot.material_type or "",
                    slot.colour_hex or "",
                    # `remain` is a percentage; the scheduler needs grams, and a
                    # 1 kg spool is the only assumption available until a lot is
                    # mounted and its real mass is known.
                    Decimal(slot.remaining_percent or 0) * Decimal(10),
                )
                for slot in printer.slots
                if slot.material_type
            ),
            in_maintenance=due,
            storage_available=printer.storage_available,
        )


def _driver_mode(mode: ConnectionMode) -> DriverConnectionMode:
    """The fleet and the driver layer name the same modes independently.

    Kept as two enums on purpose: the fleet's is a stored business fact, the
    driver's is a transport concern. This is the one place they meet.
    """
    return DriverConnectionMode(mode.value)
