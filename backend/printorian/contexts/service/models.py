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

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from printorian.contexts.service.policies import (
    FailureCause,
    FailureOrigin,
    TicketKind,
    TicketStatus,
)
from printorian.core.db import Base, Entity, UtcDateTime, enum_column
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


#: Sequential, human-quotable ticket numbers — «SV-000412» on the kit's cards.
#: A sequence rather than ``count(*) + 1`` for the reason `po_number_seq` is one:
#: two people raising tickets in the same second would otherwise share a number.
TICKET_NUMBER_SEQUENCE = Sequence("sv_number_seq", start=1, metadata=Base.metadata)


class ServiceTicket(Entity):
    """One piece of work on the shop floor: raised, worked through its steps, closed.

    A ticket is not a failure. The failure record is the *measurement* — when a
    machine stopped working and when it was seen working again — and a ticket is
    the *work*: who is fixing it, what the steps are, how long it has been going.
    A driver-opened failure gets a repair ticket raised beside it (`failure_id`),
    and the kit's «СООБЩИЛ ДРАЙВЕР» badge is that link plus `origin`.

    The kit's «Последствия» panel — what the ticket has cost in rubles — is not
    a column here and not a view over this table. It is money, behind
    `VIEW_FINANCIALS`, composed elsewhere; `schemas.py` opens with the reason.
    """

    __tablename__ = "service_tickets"
    __table_args__ = (
        UniqueConstraint("number", name="uq_service_tickets_number"),
        # The board: everything not closed, then what closed recently. Both reads
        # lead with status; `closed_at` serves the «Закрыто сегодня» lane.
        Index("ix_service_tickets_status_opened", "status", "opened_at"),
        Index("ix_service_tickets_closed_at", "closed_at"),
        # PostgreSQL does not index a foreign key for you; each is checked on the
        # parent's delete.
        Index("ix_service_tickets_printer_id", "printer_id"),
        Index("ix_service_tickets_failure_id", "failure_id"),
        Index("ix_service_tickets_assignee_id", "assignee_id"),
        Index("ix_service_tickets_opened_by", "opened_by"),
        # Time only moves forward through a ticket. Held here rather than only in
        # `TicketDesk`, because a hand-run UPDATE would otherwise put a negative
        # elapsed time on the board.
        CheckConstraint(
            "started_at IS NULL OR started_at >= opened_at", name="started_after_opened"
        ),
        CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="closed_after_opened"),
        CheckConstraint("norm_minutes IS NULL OR norm_minutes > 0", name="norm_positive"),
    )

    number: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[TicketKind] = mapped_column(enum_column(TicketKind), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        enum_column(TicketStatus), nullable=False, default=TicketStatus.OPEN
    )
    #: Who raised it — the sweep, from a state the machine reported, or a person.
    origin: Mapped[FailureOrigin] = mapped_column(enum_column(FailureOrigin), nullable=False)
    #: ``RESTRICT`` like `printer_failures.printer_id`, and nullable because a
    #: move («Снять партию с P-01 → пост PP-01») is about an order rather than a
    #: machine.
    printer_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("printers.id", ondelete="RESTRICT"), nullable=True
    )
    #: The failure a driver-opened repair is about. ``SET NULL`` rather than
    #: cascade: the failure record is the measurement and outlives the work.
    failure_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("printer_failures.id", ondelete="SET NULL"), nullable=True
    )
    #: The shop's own words — «замена сопла», «Загрузить TPU 95A в P-05». Empty
    #: for a driver-opened ticket: the backend emits no prose about itself
    #: (ADR-0012), so the client draws those from `origin` and the failure's code.
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: The kit's «НОРМА» — how long this work should take. ``NULL`` is "no norm
    #: set", never zero minutes.
    norm_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``SET NULL``, as everywhere a person is named: leaving the farm does not
    #: erase the tickets they raised or worked.
    opened_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assignee_id: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    steps: Mapped[list[ServiceTicketStep]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="ServiceTicketStep.position",
        lazy="selectin",
    )


class ServiceTicketStep(Entity):
    """One line of «Порядок работ»: what to do, how long it should take, when it was done."""

    __tablename__ = "service_ticket_steps"
    __table_args__ = (
        # `position` is the order the kit lists the steps in; two steps cannot
        # share a rung, which is what `POST …/steps/{position}/done` addresses.
        UniqueConstraint("ticket_id", "position", name="uq_service_ticket_steps_position"),
        Index("ix_service_ticket_steps_done_by", "done_by"),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint("norm_minutes IS NULL OR norm_minutes > 0", name="step_norm_positive"),
    )

    ticket_id: Mapped[EntityId] = mapped_column(
        ForeignKey("service_tickets.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    norm_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    done_by: Mapped[EntityId | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    ticket: Mapped[ServiceTicket] = relationship(back_populates="steps")


__all__ = ["TICKET_NUMBER_SEQUENCE", "PrinterFailure", "ServiceTicket", "ServiceTicketStep"]
