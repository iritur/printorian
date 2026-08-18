"""Production: turning a plan into a printer that is actually running.

The planner is pure and knows nothing about databases (ARCHITECTURE §6). This is
where its output is written down, where a job is handed to a driver, and where a
failure is recorded as a failure rather than smoothed over.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from printorian.contexts.production import events as job_events
from printorian.contexts.production import reads
from printorian.contexts.production.journal import record_event
from printorian.contexts.production.models import AssignmentRecord, PrintJob, WaitListEntry
from printorian.contexts.production.planning import (
    claim_ready_jobs,
    persist_plan,
    plate_to_send,
    to_ready_job,
)
from printorian.contexts.production.plates import attach_plate
from printorian.contexts.production.policies import (
    DISPATCH_NO_PLATE,
    DISPATCH_START_FAILED,
    DISPATCH_UPLOAD_FAILED,
    JobStatus,
    assert_transition,
)
from printorian.contexts.production.prep import pending_jobs
from printorian.contexts.production.queue import queue_position
from printorian.contexts.production.schemas import (
    CreateJob,
    JobView,
    PlanOutcome,
    QueuePosition,
)
from printorian.contexts.scheduling import (
    SchedulablePrinter,
    SchedulingPolicy,
)
from printorian.contexts.scheduling import (
    plan as make_plan,
)
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError
from printorian.core.events import EventBus
from printorian.core.ids import EntityId
from printorian.core.storage import ObjectStore
from printorian.drivers import PrinterDriver


class ProductionService:
    """Jobs, planning passes and dispatch."""

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        bus: EventBus,
        store: ObjectStore | None = None,
    ) -> None:
        self._db = session
        self._clock = clock
        self._bus = bus
        # Optional so the many tests that never dispatch need not build one.
        # `dispatch` refuses rather than guessing when it is absent — a service
        # that cannot read a plate must not send an empty one (ADR-0007).
        self._store = store

    @property
    def session(self) -> AsyncSession:
        """The session this service works in.

        Exposed for the delivery layer, which composes several contexts in one
        transaction and must not open a second one behind this service's back."""
        return self._db

    # -- creating work ---------------------------------------------------

    async def create_job(self, data: CreateJob) -> JobView:
        """Register work for a paid order. Starts `PENDING` — a plate comes first."""
        job = PrintJob(
            order_id=data.order_id,
            status=JobStatus.PENDING,
            model_asset_id=data.model_asset_id,
            model_hash=data.model_hash,
            scale=data.scale,
            material_type=data.material_type,
            colors=list(data.colors),
            width_mm=data.width_mm,
            depth_mm=data.depth_mm,
            height_mm=data.height_mm,
            nozzle_diameter_mm=data.nozzle_diameter_mm,
            grams_required=data.grams_required,
            estimated_minutes=data.estimated_minutes,
            due_at=data.due_at,
            priority=data.priority,
        )
        self._db.add(job)
        await self._db.flush()
        await record_event(self._db, job, JobStatus.PENDING, reason="job.created")
        return await self.get(job.id)

    async def mark_ready(self, job_id: EntityId, *, plate_filename: str) -> JobView:
        """A prepared plate exists, so the scheduler may pick this up."""
        job = await self._load(job_id)
        assert_transition(job.status, JobStatus.READY)
        previous = job.status
        job.status = JobStatus.READY
        job.plate_filename = plate_filename
        await self._db.flush()
        await record_event(
            self._db, job, JobStatus.READY, reason="plate.prepared", previous=previous
        )
        await self._bus.publish(job_events.JobReady(job_id=job.id, order_id=job.order_id))
        return await self.get(job.id)

    # -- the prep queue --------------------------------------------------

    async def prep_queue(self) -> list[JobView]:
        """Jobs waiting for an engineer to slice them (ADR-0006)."""
        return [JobView.model_validate(job) for job in await pending_jobs(self._db)]

    async def attach_prepared_plate(
        self,
        job_id: EntityId,
        *,
        plate_id: EntityId,
        filename: str,
        print_minutes: Decimal,
        total_grams: Decimal,
        quoted_cost: Decimal,
        prepared_cost: Decimal,
        tolerance: Decimal,
    ) -> JobView:
        """Give a job its plate and apply the variance band (ADR-0013).

        Costs are supplied rather than computed here: pricing owns what a plate
        costs, production owns what to do about it.
        """
        job = await self._load(job_id)
        await attach_plate(
            self._db,
            self._bus,
            job,
            plate_id=plate_id,
            filename=filename,
            print_minutes=print_minutes,
            total_grams=total_grams,
            quoted_cost=quoted_cost,
            prepared_cost=prepared_cost,
            tolerance=tolerance,
        )
        return await self.get(job.id)

    async def release_hold(self, job_id: EntityId, *, reason: str = "price.approved") -> JobView:
        """Let a held job through once somebody has settled the price."""
        job = await self._load(job_id)
        assert_transition(job.status, JobStatus.READY)
        previous = job.status
        job.status = JobStatus.READY
        await self._db.flush()
        await record_event(self._db, job, JobStatus.READY, reason=reason, previous=previous)
        await self._bus.publish(job_events.JobReady(job_id=job.id, order_id=job.order_id))
        return await self.get(job.id)

    # -- planning --------------------------------------------------------

    async def plan_pass(
        self,
        printers: list[SchedulablePrinter],
        *,
        policy: SchedulingPolicy | None = None,
    ) -> PlanOutcome:
        """Run the planner over a batch of ready jobs and write down what it decided.

        Printers are supplied by the caller rather than read here: the fleet owns
        machine state, and a context reaching into another context's service is
        how the boundary starts leaking.

        The jobs come from `planning.claim_ready_jobs`, which takes a lock and a
        bounded batch. That it locks at all is the important part — two overlapping
        passes would otherwise assign one job to two machines — and the reasoning
        lives there, next to the mechanism.
        """
        jobs = await claim_ready_jobs(self._db)
        if not jobs:
            return PlanOutcome()

        by_id = {str(job.id): job for job in jobs}
        result = make_plan(
            [to_ready_job(job) for job in jobs],
            printers,
            self._clock.now(),
            policy,
        )
        await persist_plan(self._db, self._bus, result, by_id)
        return PlanOutcome(
            assigned=len(result.assignments),
            wait_listed=len(result.wait_list),
            considered=len(jobs),
        )

    # -- dispatch --------------------------------------------------------

    async def dispatch(self, job_id: EntityId, driver: PrinterDriver | None) -> JobView:
        """Send an assigned job to its machine.

        Every failure — no plate, no driver, a refused upload or start — leaves the
        job back in the queue with a recorded reason rather than pretending to
        print (ADR-0007). `planning.plate_to_send` holds the pre-flight.
        """
        job = await self._load(job_id)
        assert_transition(job.status, JobStatus.DISPATCHING)
        previous = job.status
        job.status = JobStatus.DISPATCHING
        await self._db.flush()
        await record_event(
            self._db, job, JobStatus.DISPATCHING, reason="dispatch.started", previous=previous
        )

        upload, refusal = await plate_to_send(self._db, self._store, job, driver)
        if upload is None or driver is None or job.printer_id is None:
            return await self._return_to_queue(job, refusal or DISPATCH_NO_PLATE)
        # Bound here rather than later: the guard above proves it is set, and this
        # is what lets the event carry a non-optional printer.
        printer_id = job.printer_id

        try:
            reference = await driver.upload(upload)
        except Exception:
            return await self._return_to_queue(job, DISPATCH_UPLOAD_FAILED)

        try:
            handle = await driver.start(reference, {})
        except Exception:
            return await self._return_to_queue(job, DISPATCH_START_FAILED)

        job.status = JobStatus.PRINTING
        job.remote_path = getattr(reference, "path", None)
        job.job_handle = getattr(handle, "value", None)
        job.started_at = self._clock.now()
        await self._db.flush()
        await record_event(self._db, job, JobStatus.PRINTING, reason="dispatch.started_printing")
        await self._bus.publish(
            job_events.JobStarted(job_id=job.id, order_id=job.order_id, printer_id=printer_id)
        )
        return await self.get(job.id)

    async def _return_to_queue(self, job: PrintJob, code: str) -> JobView:
        previous = job.status
        job.status = JobStatus.READY
        job.printer_id = None
        job.failure_code = code
        await self._db.flush()
        await record_event(
            self._db,
            job,
            JobStatus.READY,
            reason="dispatch.failed",
            previous=previous,
            details={"code": code},
        )
        return await self.get(job.id)

    # -- running ---------------------------------------------------------

    async def record_progress(self, job_id: EntityId, *, percent: int) -> JobView:
        job = await self._load(job_id)
        job.progress_percent = max(0, min(100, percent))
        await self._db.flush()
        return await self.get(job.id)

    async def complete(self, job_id: EntityId) -> JobView:
        job = await self._load(job_id)
        assert_transition(job.status, JobStatus.SUCCEEDED)
        previous = job.status
        printer_id = job.printer_id
        job.status = JobStatus.SUCCEEDED
        job.finished_at = self._clock.now()
        job.progress_percent = 100
        # The machine is released here, not when the plate is cleared: a finished
        # print still occupying a bed is the fleet's problem to report, not a
        # reason for this job to keep holding an assignment.
        job.printer_id = None
        await self._db.flush()
        await record_event(
            self._db, job, JobStatus.SUCCEEDED, reason="print.finished", previous=previous
        )
        await self._bus.publish(
            job_events.JobSucceeded(job_id=job.id, order_id=job.order_id, printer_id=printer_id)
        )
        if printer_id is not None:
            await self._bus.publish(job_events.PrinterBecameFree(printer_id=printer_id))
        return await self.get(job.id)

    async def fail(self, job_id: EntityId, *, code: str) -> JobView:
        """A print that started and did not finish. Material and hours were spent."""
        job = await self._load(job_id)
        assert_transition(job.status, JobStatus.FAILED)
        previous = job.status
        printer_id = job.printer_id
        job.status = JobStatus.FAILED
        job.failure_code = code
        job.finished_at = self._clock.now()
        job.printer_id = None
        await self._db.flush()
        await record_event(
            self._db,
            job,
            JobStatus.FAILED,
            reason="print.failed",
            previous=previous,
            details={"code": code},
        )
        await self._bus.publish(
            job_events.JobFailed(
                job_id=job.id,
                order_id=job.order_id,
                printer_id=printer_id,
                failure_code=code,
                attempt=job.attempt,
            )
        )
        if printer_id is not None:
            await self._bus.publish(job_events.PrinterBecameFree(printer_id=printer_id))
        return await self.get(job.id)

    async def remake(self, job_id: EntityId) -> JobView:
        """Re-queue a failed job as a further attempt on the same order."""
        job = await self._load(job_id)
        assert_transition(job.status, JobStatus.READY)
        previous = job.status
        job.status = JobStatus.READY
        job.attempt += 1
        job.progress_percent = None
        job.started_at = None
        job.finished_at = None
        job.job_handle = None
        await self._db.flush()
        await record_event(
            self._db,
            job,
            JobStatus.READY,
            reason="print.remake",
            previous=previous,
            details={"attempt": job.attempt},
        )
        await self._bus.publish(job_events.JobReady(job_id=job.id, order_id=job.order_id))
        return await self.get(job.id)

    async def cancel(self, job_id: EntityId, *, reason: str = "") -> JobView:
        job = await self._load(job_id)
        assert_transition(job.status, JobStatus.CANCELLED)
        previous = job.status
        printer_id = job.printer_id
        job.status = JobStatus.CANCELLED
        job.printer_id = None
        job.finished_at = self._clock.now()
        await self._db.flush()
        await record_event(
            self._db, job, JobStatus.CANCELLED, reason=reason or "job.cancelled", previous=previous
        )
        if printer_id is not None:
            await self._bus.publish(job_events.PrinterBecameFree(printer_id=printer_id))
        return await self.get(job.id)

    async def queue_position(self, order_id: EntityId) -> QueuePosition | None:
        """Where an order's work stands, for the customer's cabinet (C7)."""
        return await queue_position(self._db, order_id)

    # -- reading ---------------------------------------------------------

    async def get(self, job_id: EntityId) -> JobView:
        job = await self._load(job_id)
        return JobView.model_validate(job)

    async def wait_list(self) -> list[WaitListEntry]:
        return await reads.wait_list(self._db)

    async def decisions_for(self, job_id: EntityId) -> list[AssignmentRecord]:
        return await reads.decisions_for(self._db, job_id)

    # -- internals -------------------------------------------------------

    async def _load(self, job_id: EntityId) -> PrintJob:
        job = await self._db.scalar(
            select(PrintJob)
            .where(PrintJob.id == job_id)
            .options(selectinload(PrintJob.events))
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise NotFoundError("error.production.job_not_found", job_id=str(job_id))
        return job
