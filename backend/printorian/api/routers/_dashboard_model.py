"""The farm summary, assembled from five contexts.

Composition belongs in the delivery layer (ARCHITECTURE §layering) — contexts do
not import each other, so the one place a printer's zone can meet a material's
committed mass is here. Kept out of the router file so the route stays four lines
and this stays readable.

**One request, not nine.** The dashboard answers four questions at once and every
panel on it is read against the same instant; nine concurrent requests would let
the KPI tiles and the status wall disagree by a few seconds, which on a screen
whose whole job is "what is happening right now" is worse than being a moment
slower. It is also what lets the live stream simply mean "refetch".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.fleet import (
    FleetOccupancy,
    FleetService,
    HeatRow,
    PrinterTable,
    PrinterView,
    StatusCount,
    hourly_load,
    occupancy,
)
from printorian.contexts.inventory import MaterialStock, headroom
from printorian.contexts.ordering import (
    FinanceOverview,
    OrdersOverview,
    Period,
    Window,
    finance_overview,
    numbers_for,
    orders_overview,
    window_for,
)
from printorian.contexts.production import (
    CommittedMaterial,
    Schedule,
    Throughput,
    committed_material,
    schedule,
    throughput,
    wait_list_size,
)
from printorian.core.ids import EntityId
from printorian.drivers import PrinterState

#: Where a machine with no recorded location is shown. A zone rather than a
#: hidden bucket: the status wall's promise is that its layout matches the shop
#: floor, and a machine missing from it entirely is the one nobody walks to.
UNZONED = ""

#: States that mean the machine is doing paid work. Used for the utilisation
#: figure, which is otherwise the most easily inflated number on the screen.
WORKING: frozenset[PrinterState] = frozenset({PrinterState.PRINTING})


class WallNode(BaseModel):
    """One glowing square. Everything needed to draw it, and nothing else."""

    id: EntityId
    name: str
    state: PrinterState
    progress_percent: int | None = None
    eta: datetime | None = None
    current_job: str | None = None
    needs_attention: bool = False
    maintenance_due: bool = False
    last_seen_at: datetime | None = None


class Zone(BaseModel):
    """One shop area, with its own load figure."""

    name: str
    nodes: list[WallNode] = Field(default_factory=list)
    #: Machines printing, as a share of the machines in this zone.
    load_percent: Decimal = Decimal(0)


class FleetOverview(BaseModel):
    """The status wall and the four figures beside it."""

    zones: list[Zone] = Field(default_factory=list)
    counts: list[StatusCount] = Field(default_factory=list)
    total: int
    printing: int
    attention: int
    #: Machines printing **right now**, as a share of the roster. Instantaneous,
    #: unlike `occupancy.current.load` beside it, which is the window's measured
    #: ratio — the client must label the delta period-over-period or the tile reads
    #: as "utilisation moved six points in the last second".
    utilisation_percent: Decimal
    #: Outcomes only, from `print_jobs`. Occupancy moved to `occupancy` below when
    #: it stopped being derived from booked job time.
    throughput: Throughput
    #: Measured hours for the KPI window and for the window before it, from
    #: `metric_rollups`. Replaces `Throughput.run_hours` / `capacity_hours` /
    #: `idle_hours`, where idle was a residual against today's roster rather than
    #: an observation.
    occupancy: FleetOccupancy = Field(default_factory=FleetOccupancy)
    #: Seven days × 24 hours of *measured* load, for the kit's load map. Its point
    #: is the shape: a farm idle every night has capacity nobody is selling, and
    #: that is invisible in a daily total. A cell whose `load` is null was never
    #: summarised and must render as neither dark nor bright.
    hourly_load: list[HeatRow] = Field(default_factory=list)


class Alert(BaseModel):
    """One row of "требует внимания".

    ``code`` is an identifier the client renders (ADR-0012); ``subject`` is the
    machine or material it is about, which is a name the farm chose and therefore
    not a translatable string.
    """

    code: str
    tone: str
    subject: str
    subject_id: EntityId | None = None
    at: datetime | None = None
    #: Structured facts the client folds into its own sentence — grams left, hours
    #: overdue. Never prose.
    detail: dict[str, str] = Field(default_factory=dict)


class FilamentBar(BaseModel):
    """One material's three-way split, plus what it means for the queue."""

    code: str
    name: str
    color_hex: str
    loaded_grams: Decimal
    stock_grams: Decimal
    committed_grams: Decimal
    #: Loaded plus stock, less what the queue has taken. Negative means the farm
    #: has promised filament it does not hold, which is the alert above.
    free_grams: Decimal
    committed_jobs: int
    loaded_printer_ids: list[EntityId] = Field(default_factory=list)


class FarmSummary(BaseModel):
    """Everything the dashboard draws, read at one instant."""

    at: datetime
    window: Window
    orders: OrdersOverview
    finance: FinanceOverview
    fleet: FleetOverview
    schedule: Schedule
    filament: list[FilamentBar] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    wait_list: int = 0


async def farm_summary(
    db: AsyncSession, fleet: FleetService, *, now: datetime, period: Period
) -> FarmSummary:
    """Read every panel of the dashboard against one instant."""
    window = window_for(period, now)
    table = await fleet.table()
    stocks = await headroom(db)
    committed = {row.material_code: row for row in await committed_material(db)}

    return FarmSummary(
        at=now,
        window=window,
        orders=await orders_overview(db, window),
        finance=await finance_overview(db, window, now),
        fleet=FleetOverview(
            zones=_zones(table.rows),
            counts=table.counts,
            total=table.total,
            printing=_printing(table.rows),
            attention=table.attention,
            utilisation_percent=_percent(_printing(table.rows), table.total),
            throughput=await throughput(db, since=window.start, until=window.end),
            # Composed here rather than inside either context: the fleet may not
            # know what an ordering `Window` is, and this is the layer that is
            # allowed to know both (ARCHITECTURE §layering).
            occupancy=FleetOccupancy(
                current=await occupancy(db, since=window.start, until=window.end),
                previous=await occupancy(db, since=window.previous_start, until=window.start),
            ),
            hourly_load=await hourly_load(db, now=now),
        ),
        schedule=await _named_schedule(db, now=now),
        filament=[_bar(stock, committed) for stock in stocks],
        alerts=_alerts(table, stocks, committed),
        wait_list=await wait_list_size(db),
    )


async def _named_schedule(db: AsyncSession, *, now: datetime) -> Schedule:
    """The strip, with each bar labelled by its order's number rather than its id.

    Production returns the strip with `order_number` blank because it is not
    allowed to know what an order is called. Resolving it is composition, and
    composition is this layer's job — one extra query for the whole screen.
    """
    strip = await schedule(db, now=now)
    bars = [bar for row in strip.rows for bar in row.bars]
    numbers = await numbers_for(db, [bar.order_id for bar in bars])
    for bar in bars:
        bar.order_number = numbers.get(bar.order_id, "")
    return strip


# ------------------------------------------------------------------- shaping


def _zones(rows: list[PrinterView]) -> list[Zone]:
    """Group the wall by shop area, preserving the fleet's own row order.

    Position on the wall is supposed to say *where*, so the grouping key is the
    printer's recorded location and the order inside a zone is the order the fleet
    table returns — which is by name, and therefore stable between refreshes. A
    wall whose squares moved when a machine changed state would be unreadable
    precisely when it matters.
    """
    grouped: dict[str, list[WallNode]] = defaultdict(list)
    for row in rows:
        grouped[(row.location or UNZONED).strip()].append(
            WallNode(
                id=row.id,
                name=row.name,
                state=row.state,
                progress_percent=row.progress_percent,
                eta=row.eta,
                current_job=row.current_job,
                needs_attention=row.needs_attention,
                maintenance_due=row.maintenance_due,
                last_seen_at=row.last_seen_at,
            )
        )
    return [
        Zone(
            name=name,
            nodes=nodes,
            load_percent=_percent(sum(1 for node in nodes if node.state in WORKING), len(nodes)),
        )
        # Named zones first and alphabetically; the unzoned bucket last, where it
        # reads as "and these" rather than as the farm's first shop.
        for name, nodes in sorted(grouped.items(), key=lambda item: (item[0] == UNZONED, item[0]))
    ]


def _printing(rows: list[PrinterView]) -> int:
    return sum(1 for row in rows if row.state in WORKING)


def _percent(part: int, whole: int) -> Decimal:
    if not whole:
        return Decimal(0)
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1"))


def _bar(stock: MaterialStock, committed: dict[str, CommittedMaterial]) -> FilamentBar:
    entry = committed.get(stock.code)
    grams = entry.grams if entry else Decimal(0)
    jobs = entry.job_count if entry else 0
    return FilamentBar(
        code=stock.code,
        name=stock.name,
        color_hex=stock.color_hex,
        loaded_grams=stock.loaded_grams,
        stock_grams=stock.stock_grams,
        committed_grams=grams,
        free_grams=stock.total_grams - grams,
        committed_jobs=jobs,
        loaded_printer_ids=[EntityId(value) for value in stock.loaded_printer_ids],
    )


def _alerts(
    table: PrinterTable, stocks: list[MaterialStock], committed: dict[str, CommittedMaterial]
) -> list[Alert]:
    """What a person should walk to, worst first.

    Derived rather than stored. An alerts table would need its own lifecycle —
    raised, acknowledged, cleared — and every one of these conditions is already
    true or false in the data; a machine that came back online should stop being
    an alert because it is online, not because somebody remembered to close a row.
    """
    found: list[Alert] = []
    for row in table.rows:
        if row.state is PrinterState.ERROR:
            found.append(_printer_alert("dashboard.alert.printer_error", "bad", row))
        elif row.state is PrinterState.OFFLINE and row.is_active:
            found.append(_printer_alert("dashboard.alert.printer_offline", "warn", row))
        elif row.maintenance_due:
            found.append(_printer_alert("dashboard.alert.maintenance_due", "warn", row))
        elif row.needs_attention:
            found.append(_printer_alert("dashboard.alert.printer_attention", "live", row))

    for stock in stocks:
        entry = committed.get(stock.code)
        grams = entry.grams if entry else Decimal(0)
        free = stock.total_grams - grams
        if free >= 0:
            continue
        found.append(
            Alert(
                code="dashboard.alert.material_oversold",
                tone="bad",
                subject=stock.code,
                detail={
                    "short_grams": str(-free),
                    "committed_grams": str(grams),
                    "held_grams": str(stock.total_grams),
                },
            )
        )

    order = {"bad": 0, "warn": 1, "live": 2}
    return sorted(found, key=lambda alert: order.get(alert.tone, 3))


def _printer_alert(code: str, tone: str, row: PrinterView) -> Alert:
    return Alert(
        code=code,
        tone=tone,
        subject=row.name,
        subject_id=row.id,
        at=row.last_seen_at,
        detail={"state": row.state.value},
    )


__all__ = [
    "Alert",
    "FarmSummary",
    "FilamentBar",
    "FleetOverview",
    "WallNode",
    "Zone",
    "farm_summary",
]
