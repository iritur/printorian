"""Persistence for jobs and for the scheduler's decisions.

The planner is pure and has no database (ARCHITECTURE §6), exactly as the pricing
engine has none. Its output is persisted here, next to the jobs those decisions are
about — the same arrangement as a pinned price breakdown living with the order.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.production.policies import JobStatus
from printorian.core.db import Entity, JsonB, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class PrintJob(Entity):
    """One plate, on its way to one machine."""

    __tablename__ = "print_jobs"
    __table_args__ = (
        Index("ix_print_jobs_status_created_at", "status", "created_at"),
        Index("ix_print_jobs_printer_id", "printer_id"),
        Index("ix_print_jobs_order_id", "order_id"),
        # The planner's query, and the hottest read in the system — it runs on every
        # tick and on every event that could change the answer. A partial index over
        # just the ready jobs stays the size of the queue rather than the size of all
        # production history, which means it stays in cache permanently.
        Index(
            "ix_print_jobs_ready_priority",
            "priority",
            "created_at",
            postgresql_where=text(f"status = '{JobStatus.READY.value}'"),
        ),
        # "which jobs printed from this plate" — asked when a plate goes stale —
        # and the index the plate's `SET NULL` delete needs.
        Index("ix_print_jobs_prepared_plate_id", "prepared_plate_id"),
        # The index the `RESTRICT` check reads when retention considers a model.
        Index("ix_print_jobs_model_asset_id", "model_asset_id"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("scale > 0", name="scale_positive"),
        CheckConstraint("grams_required >= 0", name="grams_non_negative"),
        CheckConstraint("estimated_minutes >= 0", name="estimated_minutes_non_negative"),
        CheckConstraint(
            "progress_percent IS NULL OR (progress_percent BETWEEN 0 AND 100)",
            name="progress_range",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
    )

    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus), nullable=False, default=JobStatus.PENDING
    )
    #: Set while the job holds a machine; cleared when it lets go.
    printer_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("printers.id", ondelete="SET NULL"), nullable=True
    )

    #: How many times this has been attempted. A remake increments it rather than
    #: creating a second job, so the history stays attached to what was ordered.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # -- what is being printed -------------------------------------------
    #: The geometry, so the prep queue can offer it for download.
    #:
    #: ``RESTRICT``: a job still waiting to print protects the mesh it needs, which
    #: is the same guarantee `order_lines` gives and for the same reason — retention
    #: must not collect a model the farm is about to use.
    model_asset_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("model_assets.id", ondelete="RESTRICT"), nullable=True
    )
    #: The digest `plate_key` is built from. Denormalised from the asset on purpose:
    #: it is the cache key, and it must keep working after retention has collected
    #: the mesh — a plate stays valid even when its source is gone.
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: Also a `plate_key` input: the same mesh at two scales is two plates.
    scale: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal(1))

    # -- what the machine has to be able to do ---------------------------
    material_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    colors: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)
    width_mm: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    depth_mm: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    height_mm: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0))
    nozzle_diameter_mm: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    grams_required: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    estimated_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )

    # -- the prepared plate ----------------------------------------------
    plate_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: Where the plate landed on the printer, from the driver.
    remote_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The machine's own id for the running job, for reconciling telemetry.
    job_handle: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # -- scheduling inputs -----------------------------------------------
    due_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    progress_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: An error *code*, never prose (ADR-0012).
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: The cached plate this job prints from, once one exists (ADR-0006). Null
    #: while the job is still waiting for an engineer.
    prepared_plate_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("prepared_plates.id", ondelete="SET NULL"), nullable=True
    )

    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobEvent.sequence",
    )


class JobEvent(Entity):
    """One recorded step in a job's life."""

    __tablename__ = "job_events"
    __table_args__ = (
        # This table had **no index at all**. PostgreSQL does not index a foreign
        # key for you, so every job detail view — and every cascading delete of a
        # job — was a sequential scan over the whole of production history.
        #
        # Unique for the same reason as `OrderEvent`: `sequence` is the documented
        # ordering and nothing enforced it.
        UniqueConstraint("job_id", "sequence", name="uq_job_events_job_id_sequence"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
    )

    job_id: Mapped[EntityId] = mapped_column(
        ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: Explicit ordering. Neither `created_at` nor the UUIDv7 key is reliable —
    #: the same reasoning as `OrderEvent.sequence`.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)

    job: Mapped[PrintJob] = relationship(back_populates="events")


class AssignmentRecord(Entity):
    """Why a job went where it went — the planner's audit record, persisted.

    Written for *every* planning outcome, including jobs that were not assigned.
    "Why did job #4127 go to P1S-03?" has to be answerable from the database, and
    answering it means keeping the machines that lost and the grounds on which they
    lost. V1 could not answer it because it never asked.
    """

    __tablename__ = "assignment_records"
    __table_args__ = (
        Index("ix_assignment_records_job_id_created_at", "job_id", "created_at"),
        # "What did the planner just decide, across the farm" — the console's view,
        # which has no job id to filter on.
        Index("ix_assignment_records_created_at", text("created_at DESC")),
    )

    job_id: Mapped[EntityId] = mapped_column(
        ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: Typed as a UUID — it was ``String(64)``, holding a UUID spelled as text —
    #: but deliberately **not** a foreign key, unlike every other id in this schema.
    #:
    #: A foreign key here would have to choose a delete rule, and both are wrong.
    #: ``SET NULL`` erases the answer to the only question this table exists to
    #: answer: decommission a machine and every historical "job #4127 went to
    #: P1S-03" silently becomes "went to nothing". ``RESTRICT`` is worse — it makes
    #: retiring a printer impossible while any decision mentioning it survives.
    #:
    #: The same reasoning already governs ``candidates`` below, which stores printer
    #: ids as bare strings for exactly this reason. This is an immutable record of
    #: one moment, and a moment does not change when the farm does.
    chosen_printer_id: Mapped[EntityId | None] = mapped_column(nullable=True)
    #: Every candidate: its id, whether it was eligible, its rejection reasons and
    #: its score components. Stored whole rather than normalised — it is an
    #: immutable record of one moment, never queried field by field.
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, nullable=False, default=list)
    winning_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)


class WaitListEntry(Entity):
    """A job nothing can take yet, and why.

    One row per job — a job is either waiting or it is not, and re-planning
    replaces the row rather than appending, so the cabinet never shows a customer
    a stale reason alongside a current one.
    """

    __tablename__ = "wait_list_entries"
    __table_args__ = (
        Index("ix_wait_list_entries_job_id", "job_id", unique=True),
        Index("ix_wait_list_entries_order_id", "order_id"),
        # The customer's queue position counts entries ahead of theirs by predicted
        # start, so that is what the index is ordered on.
        Index("ix_wait_list_entries_predicted_start", "predicted_start"),
    )

    job_id: Mapped[EntityId] = mapped_column(
        ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Only set when time alone will resolve the wait. Null when the job needs a
    #: person — filament to be mounted, or a decision about a plate no machine can
    #: print. Inventing a date for those is the queue's version of fake telemetry.
    predicted_start: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    blocking_reasons: Mapped[list[str]] = mapped_column(JsonB, nullable=False, default=list)


class EstimateVariance(Entity):
    """How far the quote was from what slicing actually found.

    ADR-0013 requires every variance to be recorded, whether or not it exceeded
    tolerance. The ones inside the band are the interesting data: they are the farm
    absorbing small differences, and they are what Phase 6 calibrates the mesh
    estimator against. Keeping only the escalations would leave the estimator
    learning from its worst cases alone.
    """

    __tablename__ = "estimate_variances"
    __table_args__ = (
        Index("ix_estimate_variances_job_id", "job_id"),
        Index("ix_estimate_variances_order_id", "order_id"),
        CheckConstraint("tolerance >= 0", name="tolerance_non_negative"),
    )

    job_id: Mapped[EntityId] = mapped_column(
        ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[EntityId] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )

    quoted_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    prepared_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tolerance: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    within_tolerance: Mapped[bool] = mapped_column(nullable=False, default=True)

    #: The manufacturing numbers behind the money, so the estimator can be
    #: calibrated on what it actually predicts rather than on a price.
    estimated_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    prepared_minutes: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    estimated_grams: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
    prepared_grams: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal(0)
    )
