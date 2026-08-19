"""What the farm can honestly say about itself in public.

The promo page's argument is that this shop publishes real figures with the
method behind them. That makes hardcoding those figures the one thing it must not
do: a landing page asserting "96% on time" as decoration, on a product whose
pitch is that nobody else shows their working, is the defect ADR-0007 forbids in
the drivers wearing a marketing hat.

So every number here is counted from the tables, and a farm with no history
reports **nothing** rather than a plausible zero. `None` and `0%` are different
claims — the first says "we have not run long enough to tell you", the second
says "we measured, and it was none", and only one of them is true on day one.

**Why this sits in the delivery layer.** It counts across `ordering` *and*
`production`, and a context may not import another (ARCHITECTURE §layering,
enforced by `import-linter`). Composing two contexts is the job of the layer
above them — the same reason the scheduler tick lives in `workers` rather than
inside `fleet` or `production`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.catalog.catalogue import CatalogModel
from printorian.contexts.fleet.models import Printer
from printorian.contexts.ordering.models import Order
from printorian.contexts.ordering.policies import OrderStatus
from printorian.contexts.production.models import PrintJob
from printorian.contexts.production.policies import JobStatus
from printorian.drivers.base import PrinterState

#: The window the promo page quotes. Ninety days is long enough to smooth a bad
#: week and short enough that it describes the farm as it is now.
DEFAULT_WINDOW = timedelta(days=90)

#: Orders that reached the customer. Anything earlier is work in progress, and
#: counting it as "completed" would inflate the headline figure.
_DELIVERED = (OrderStatus.SHIPPED, OrderStatus.COMPLETED)


@dataclass(frozen=True, slots=True)
class FarmStats:
    """Measured facts. Every field is ``None`` when there is nothing to measure."""

    window_days: int
    orders_delivered: int
    #: Share of delivered orders that arrived by the date promised, 0–100.
    on_time_percent: Decimal | None
    print_hours: Decimal | None
    #: Share of finished jobs that failed, 0–100.
    failure_percent: Decimal | None

    # -- live, rather than windowed --------------------------------------
    #: Machines the farm runs, and how many are printing this second.
    #:
    #: A capacity figure, and publishing it is a deliberate choice rather than an
    #: oversight: the kit puts «сейчас печатается 7 из 12» at the top of the
    #: outward-facing page, and a shop whose argument is that its numbers can be
    #: checked cannot make the busiest one of them a secret. What stays private
    #: is *which* machines and where they stand — that is the console's, and the
    #: console is not on the internet (ADR-0016).
    printers_total: int = 0
    printers_printing: int = 0
    #: Minutes until a machine is free: `0` when one already is, otherwise the
    #: shortest remaining print. ``None`` when no machine has reported one —
    #: which is not "immediately", and the page must not round it to that.
    next_free_minutes: int | None = None
    #: Published models in the catalogue, for the «N готовых моделей» button.
    catalog_models: int = 0

    @property
    def has_history(self) -> bool:
        """Whether the farm has run long enough for any of this to mean anything."""
        return self.orders_delivered > 0


async def farm_stats(
    db: AsyncSession, *, now: datetime, window: timedelta = DEFAULT_WINDOW
) -> FarmStats:
    """Count what the farm did over the window ending at ``now``.

    ``now`` is an argument rather than a clock read, so the figures on a page are
    reproducible from a timestamp when somebody disputes them — the same reason
    the planner takes one.
    """
    since = now - window

    total = int(
        await db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.status.in_(_DELIVERED),
                Order.shipped_at.is_not(None),
                Order.shipped_at >= since,
            )
        )
        or 0
    )

    on_time_count = int(
        await db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.status.in_(_DELIVERED),
                Order.shipped_at.is_not(None),
                Order.shipped_at >= since,
                # An order with no promise cannot be late. Excluding it keeps the
                # percentage honest rather than crediting the farm for silence.
                Order.promised_at.is_not(None),
                Order.shipped_at <= Order.promised_at,
            )
        )
        or 0
    )
    promised = int(
        await db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.status.in_(_DELIVERED),
                Order.shipped_at.is_not(None),
                Order.shipped_at >= since,
                Order.promised_at.is_not(None),
            )
        )
        or 0
    )

    # Summed in Python rather than in SQL. `extract('epoch', a - b)` is
    # PostgreSQL-only and silently computes nonsense on SQLite, which is what the
    # unit tests run on — a figure that is wrong only in one environment is worse
    # than a query that is slightly less clever in both. Bounded by finished jobs
    # in the window, which on a farm this size is thousands, not millions.
    spans = await db.execute(
        select(PrintJob.started_at, PrintJob.finished_at).where(
            PrintJob.started_at.is_not(None),
            PrintJob.finished_at.is_not(None),
            PrintJob.finished_at >= since,
        )
    )
    rows = [(began, ended) for began, ended in spans if began and ended]
    seconds = sum((ended - began).total_seconds() for began, ended in rows) if rows else None

    finished = int(
        await db.scalar(
            select(func.count())
            .select_from(PrintJob)
            .where(
                PrintJob.status.in_((JobStatus.SUCCEEDED, JobStatus.FAILED)),
                PrintJob.finished_at.is_not(None),
                PrintJob.finished_at >= since,
            )
        )
        or 0
    )
    failed = int(
        await db.scalar(
            select(func.count())
            .select_from(PrintJob)
            .where(
                PrintJob.status == JobStatus.FAILED,
                PrintJob.finished_at.is_not(None),
                PrintJob.finished_at >= since,
            )
        )
        or 0
    )

    fleet = await _fleet(db)
    catalogue = int(
        await db.scalar(
            select(func.count())
            .select_from(CatalogModel)
            .where(CatalogModel.published_at.is_not(None))
        )
        or 0
    )

    return FarmStats(
        window_days=window.days,
        orders_delivered=total,
        on_time_percent=_percent(on_time_count, promised),
        print_hours=(
            (Decimal(str(seconds)) / Decimal(3600)).quantize(Decimal("0.1"))
            if seconds is not None
            else None
        ),
        failure_percent=_percent(failed, finished),
        printers_total=fleet[0],
        printers_printing=fleet[1],
        next_free_minutes=fleet[2],
        catalog_models=catalogue,
    )


async def _fleet(db: AsyncSession) -> tuple[int, int, int | None]:
    """How many machines the farm runs, how many are busy, and the shortest wait.

    Counted over *active* printers only. A machine somebody has retired is not
    capacity the customer can be offered, and including it would inflate the
    denominator of the one figure on the page that describes right now.

    The wait comes from each machine's own last report — never from an estimate.
    A printer that has not said how long it has left contributes nothing, so a
    farm whose telemetry is down reports `None` rather than a comforting number.
    That is ADR-0007's rule, applied where it is most tempting to break.
    """
    printers = list(await db.scalars(select(Printer).where(Printer.is_active.is_(True))))
    printing = [p for p in printers if p.state is PrinterState.PRINTING]

    # A machine standing idle *is* the answer to "when is one free", and it is
    # nought minutes rather than an absent figure.
    if any(p.state is PrinterState.IDLE for p in printers):
        return len(printers), len(printing), 0

    remaining = [
        int(value)
        for value in (
            (printer.last_telemetry or {}).get("remaining_minutes") for printer in printing
        )
        if value is not None
    ]
    return len(printers), len(printing), min(remaining) if remaining else None


def _percent(part: int, whole: int) -> Decimal | None:
    """A share, or nothing when there is no denominator.

    Zero out of zero is not zero percent — it is a question nobody has answered
    yet, and rendering it as `0%` on a public page is a claim the farm cannot
    support.
    """
    if whole <= 0:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"))


__all__ = ["DEFAULT_WINDOW", "FarmStats", "farm_stats"]
