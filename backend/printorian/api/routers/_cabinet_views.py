"""What the cabinet is told about the machine printing an order.

Composed here rather than in `production`, for the reason that context's queue
module already gives: it knows a `printer_id` and nothing about what a printer
*is*. Resolving one to a name and its last report crosses into `fleet`, and the
API layer is the only one allowed to know both.

**What is deliberately not here: where the machine physically stands.** The kit
prints «ПРИНТЕР :: P-01 · BAMBU X1C · ЦЕХ A СТОЙКА 1», and the first two thirds
of that are here. The rack is not. The console is served from the farm's own
network precisely so its internals stay off the internet (ADR-0016), and a
storefront that publishes the floor plan one order at a time undoes that a little
at a time. Which machine and which model is the customer's business — it is their
part on it, and the farm's whole pitch is that the figures can be checked. Which
rack it sits in answers nothing they asked.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from printorian.contexts.production import QueuePosition


class Machine(BaseModel):
    """The printer running an order, as its last report describes it.

    Every field below `model` comes from the machine itself and is `None` until
    it has said so. That is the point: a driver never invents a figure
    (ADR-0007), so a printer that has not reported has no progress rather than
    zero progress, and the cabinet shows «—».
    """

    name: str
    brand: str
    model: str
    state: str

    progress_percent: int | None = None
    remaining_minutes: int | None = None
    #: Absolute finish time, computed once here rather than by every client from
    #: a countdown that is already stale by the time it arrives.
    eta: datetime | None = None
    #: A count rather than a percentage, and the difference matters: «слой 412 /
    #: 654» distinguishes a slow print from a stalled one, and «63%» does not.
    layer_current: int | None = None
    layer_total: int | None = None


class OrderProgress(BaseModel):
    """Where an order's work stands, and on what.

    One object rather than two requests because the cabinet renders them as one
    panel — the pipeline's «Печатается» stage and the machine under it are the
    same fact seen twice, and letting them paint separately means the stage can
    be lit for a moment above a blank machine.
    """

    #: ``None`` when the order has no job yet. Paid but not yet prepared is a
    #: real state, not an error.
    queue: QueuePosition | None = None
    #: ``None`` when no machine has been chosen, or when the one chosen has been
    #: removed from the fleet since.
    machine: Machine | None = None
