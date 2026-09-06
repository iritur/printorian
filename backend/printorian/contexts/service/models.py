"""The failure record: one row per stretch of time a machine was not working.

One table, and the shape is chosen so the two things that make the sweep in
`workers/service.py` safe are held by PostgreSQL rather than by Python.

**A machine has at most one open failure.** That is the partial unique index
below, and it is what makes the sweep idempotent: a second pass that has not yet
seen the first pass's commit cannot mint a duplicate, and a second worker process
cannot either. A Python check ahead of the insert would be a race with itself.

**A repair cannot end before the failure began.** That is the CHECK. `restored_at`
comes from the machine's own `last_seen_at`, and a clock that jumped backwards, a
hand-run UPDATE or an import would otherwise write a negative repair time straight
into MTTR.

There is no retention on this table, deliberately, and for the reason
`metric_rollups` has none: a failure history that expires is a reliability figure
that quietly improves.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from printorian.contexts.service.policies import FailureCause, FailureOrigin
from printorian.core.db import Entity, UtcDateTime, enum_column
from printorian.core.ids import EntityId


class PrinterFailure(Entity):
    """One failure of one machine, from the moment it was noticed to the repair."""

    __tablename__ = "printer_failures"
    __table_args__ = (
        # "what did this machine do over this window", which is every read in
        # `reliability.py`. Leading with `printer_id` also serves the RESTRICT
        # check on the foreign key below, which would otherwise scan the table.
        Index("ix_printer_failures_printer_detected", "printer_id", "detected_at"),
        # The `SET NULL` on `recorded_by` is checked on every user delete, and
        # PostgreSQL does not index a foreign key for you.
        Index("ix_printer_failures_recorded_by", "recorded_by"),
        # **The load-bearing one.** At most one unrestored failure per machine, in
        # the database. See the module docstring: this is what the sweep's
        # idempotence actually rests on, and it stays true when the sweep is not
        # the writer.
        Index(
            "uq_printer_failures_open",
            "printer_id",
            unique=True,
            postgresql_where=text("restored_at IS NULL"),
        ),
        CheckConstraint(
            "restored_at IS NULL OR restored_at >= detected_at",
            name="restored_after_detected",
        ),
    )

    #: ``RESTRICT``, unlike `metric_rollups.printer_id`, which carries no key at
    #: all because ADR-0018 drops partitions underneath it. Nothing in this tree
    #: deletes a printer — the fleet retires them with `is_active` — so the rule
    #: costs nothing today and refuses the one thing that would be unrecoverable:
    #: a delete that erases the evidence a machine was unreliable. A history a
    #: delete can silently empty is not a history.
    printer_id: Mapped[EntityId] = mapped_column(
        ForeignKey("printers.id", ondelete="RESTRICT"), nullable=False
    )

    origin: Mapped[FailureOrigin] = mapped_column(enum_column(FailureOrigin), nullable=False)

    #: ``NULL`` means **nobody has named a cause**, which is the state every
    #: driver-opened failure starts in. It is reported apart from
    #: `FailureCause.OTHER` — see `policies.FailureCause`.
    cause: Mapped[FailureCause | None] = mapped_column(enum_column(FailureCause), nullable=True)

    #: What the machine said, verbatim: ``bambu.print_error.{code}``. ``NULL``
    #: means the driver reported no code, not that there was no error — a person
    #: standing at a jammed extruder records a failure the machine never noticed.
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: When the machine was *observed* to be failing, which for a driver-opened
    #: failure is the printer's `last_seen_at` and never the sweep's own clock.
    detected_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    #: The observation in which it was working again. ``NULL`` while it is still
    #: down — and that null is what the partial index above keys on.
    restored_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: Prose, in the farm's own language, for the ticket the console will draw.
    #: Not a message code: ADR-0012 governs what the backend emits about itself,
    #: not content the shop writes about its own machines.
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    #: ``SET NULL``, copying `postproduction_tasks.operator_id`: a person leaving
    #: the farm must not delete the record of what broke while they were there.
    #: ``NULL`` also means "the sweep opened this", which no user did.
    recorded_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["PrinterFailure"]
