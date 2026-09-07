"""Whether anything is on a machine right now, for something about to cut power.

A farm host patches itself and reboots. `unattended-upgrades` will do it at 06:00,
a UPS on a dying battery will do it in ninety seconds, and neither of them knows
what a print is. A twelve-hour plate killed at hour eleven is not an outage that
heals — it is spent filament, a spent bed and an order that has to be remade — so
the reboot has to ask first, and this module is the fact it asks about.

**The dangerous default is a confident zero.** A guard that answers "nothing is
printing" because it could not read the database has answered a question it was not
asked, and that answer authorises exactly the irreversible act. So nothing here
manufactures a count: the reading either happened or it did not, and the caller in
``/health/printing`` turns a failed read into a named `unknown` rather than into
three zeros (root CLAUDE.md §1 — ADR-0007's flattering denominator wearing
different clothes).

**Three counts, never one total**, for the same reason ``/health/workers`` reports
per printer rather than a count: a verdict that cannot name its own cause is one
somebody has to go and find. An operator told "in flight" at 06:00 needs to know
whether that is one plate at 40% or a job wedged in DISPATCHING since Friday,
because those two want opposite actions.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus

#: The statuses this module counts. Deliberately not `JobStatus.occupies_printer`,
#: which today holds the same three members and answers a different question —
#: "which jobs must be released from a machine". Reusing it would tie two decisions
#: together that are free to diverge, and the verdict below already declines to use
#: all three of these.
WATCHED = (JobStatus.ASSIGNED, JobStatus.DISPATCHING, JobStatus.PRINTING)


@dataclass(frozen=True, slots=True)
class InFlight:
    """How many jobs are at each of the three stages that involve a machine.

    Every field is a measured count. There is deliberately no "unknown" value here:
    a reading that could not be taken is the *absence* of an :class:`InFlight`, not
    an :class:`InFlight` full of zeros, and keeping that distinction in the type is
    what stops the unreadable case being answered by accident.
    """

    assigned: int
    dispatching: int
    printing: int


async def in_flight(db: AsyncSession) -> InFlight:
    """Count the jobs that are on, or on their way to, a machine.

    One grouped count over `ix_print_jobs_status_created_at`, so it costs nothing
    on a farm with years of finished jobs behind it.

    **No filter on ``printer_id``.** A row in PRINTING whose `printer_id` was
    cleared — a printer retired under a running job, the `SET NULL` on
    `print_jobs.printer_id` firing — is still a print in progress. Narrowing the
    query to rows that name a machine would drop it and answer "idle", which is
    reading an absent value as a zero on the one path where that authorises pulling
    the plug. There is a test that fails the moment such a filter appears.

    A status missing from the result is genuinely absent: the group is missing
    because no row carries it. That is the one zero in this module that is honest,
    and it is honest only because the query ran.
    """
    rows = await db.execute(
        select(PrintJob.status, func.count(PrintJob.id))
        .where(PrintJob.status.in_(WATCHED))
        .group_by(PrintJob.status)
    )
    counts = {status: int(count) for status, count in rows.all()}
    return InFlight(
        assigned=counts.get(JobStatus.ASSIGNED, 0),
        dispatching=counts.get(JobStatus.DISPATCHING, 0),
        printing=counts.get(JobStatus.PRINTING, 0),
    )


def interrupting_is_unsafe(reading: InFlight) -> bool:
    """Whether cutting power now would destroy work, given a reading that was taken.

    Two decisions, both deviations a reviewer should question, so both are written
    down here rather than left to be re-derived from the expression:

    **DISPATCHING vetoes, although issue #17 says only "printing".** An upload
    dying half-way is the one genuinely irreversible moment in the whole sequence —
    `policies.py` gives that state its own name precisely so a dead upload cannot be
    mistaken for either queued or printing — and a machine left holding half a plate
    file needs a person. Widening the veto costs a deferred reboot; narrowing it
    costs a print.

    **ASSIGNED does not veto, although it occupies a printer.** Nothing has been
    sent to a machine yet: an assignment is a plan, and `TRANSITIONS` lets it go
    back to READY for exactly that reason, so the planner simply re-makes it after a
    restart. The alternative is worse than it looks — a job wedged in ASSIGNED would
    hold the veto on for ever and the host would never patch again, the same
    permanently-red failure `/health/workers` refuses to accept from an unreachable
    printer. The count is still reported, so an operator can see why the queue is
    not empty.
    """
    return reading.dispatching > 0 or reading.printing > 0


__all__ = ["WATCHED", "InFlight", "in_flight", "interrupting_is_unsafe"]
