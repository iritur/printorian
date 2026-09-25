"""«Перевозчики» and «Сроки доставки» — computed from shipments, never typed in.

Both reads share one rule, and it is the rule the whole screen exists for: the
denominator of «В срок» and of «Точность» is the *delivered* parcels that carried
a promise. A parcel still out is neither punctual nor late; a delivered parcel
whose postcode no zone claimed has a transit time and no promise to keep. Neither
is in the denominator, and both are counted beside it so a reader can see how
much of the record the share stands on (CLAUDE.md §1).

The rows are pulled and folded in Python rather than aggregated in SQL: the
punctuality rule lives in `policies.on_time`, and one definition of "on time"
serves the board, the scorecard and the accuracy table. The volume — parcels per
quarter on a farm — is far below where that trade would matter.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.logistics.models import Shipment, ShipmentEvent
from printorian.contexts.logistics.policies import EventKind, ShipmentStatus, on_time, transit_days
from printorian.contexts.logistics.schemas import CarrierScore, ZoneAccuracy

_SHARE_PLACES = Decimal("0.0001")
_DAY_PLACES = Decimal("0.1")


def _share(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(_SHARE_PLACES)


def _mean_days(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal(0)) / Decimal(len(values))).quantize(_DAY_PLACES)


def carrier_scores(
    shipments: Sequence[Shipment], damaged_by_carrier: dict[str, int]
) -> list[CarrierScore]:
    """Fold shipments into one row per carrier. Pure."""
    by_carrier: dict[str, list[Shipment]] = {}
    for shipment in shipments:
        by_carrier.setdefault(shipment.carrier_code, []).append(shipment)
    scores: list[CarrierScore] = []
    for carrier, rows in sorted(by_carrier.items()):
        delivered = [row for row in rows if row.delivered_at is not None]
        promised = [row for row in delivered if row.promised_days is not None]
        kept = [
            row
            for row in promised
            if row.delivered_at is not None
            and on_time(row.shipped_at, row.delivered_at, row.promised_days)
        ]
        scores.append(
            CarrierScore(
                carrier_code=carrier,
                shipments=len(rows),
                delivered=len(delivered),
                promised=len(promised),
                on_time=len(kept),
                on_time_share=_share(len(kept), len(promised)),
                damaged=damaged_by_carrier.get(carrier, 0),
                returned=sum(1 for row in rows if row.status is ShipmentStatus.RETURNED),
                mean_transit_days=_mean_days(
                    [
                        transit_days(row.shipped_at, row.delivered_at)
                        for row in delivered
                        if row.delivered_at is not None
                    ]
                ),
            )
        )
    return scores


def zone_accuracy(shipments: Sequence[Shipment]) -> list[ZoneAccuracy]:
    """Fold delivered, promised shipments into one row per (zone, promise). Pure."""
    groups: dict[tuple[str, int], list[Shipment]] = {}
    for shipment in shipments:
        if (
            shipment.delivered_at is None
            or shipment.zone_code is None
            or shipment.promised_days is None
        ):
            continue
        groups.setdefault((shipment.zone_code, shipment.promised_days), []).append(shipment)
    rows: list[ZoneAccuracy] = []
    for (zone, promised_days), members in sorted(groups.items()):
        days = [
            transit_days(row.shipped_at, row.delivered_at)
            for row in members
            if row.delivered_at is not None
        ]
        kept = sum(1 for value in days if value <= Decimal(promised_days))
        rows.append(
            ZoneAccuracy(
                zone_code=zone,
                promised_days=promised_days,
                delivered=len(members),
                on_time=kept,
                accuracy=_share(kept, len(members)),
                mean_transit_days=_mean_days(days),
            )
        )
    return rows


async def shipments_since(db: AsyncSession, *, since: datetime) -> list[Shipment]:
    """Every parcel that shipped at or after ``since``, events loaded."""
    rows = await db.scalars(
        select(Shipment).where(Shipment.shipped_at >= since).order_by(Shipment.shipped_at)
    )
    return list(rows.unique().all())


async def damaged_counts(db: AsyncSession, *, since: datetime) -> dict[str, int]:
    """Recorded `DAMAGED` events per carrier over the window."""
    rows = await db.execute(
        select(Shipment.carrier_code, ShipmentEvent.id)
        .join(ShipmentEvent, ShipmentEvent.shipment_id == Shipment.id)
        .where(ShipmentEvent.kind == EventKind.DAMAGED, Shipment.shipped_at >= since)
    )
    counts: dict[str, int] = {}
    for carrier, _event in rows.all():
        counts[carrier] = counts.get(carrier, 0) + 1
    return counts


__all__ = ["carrier_scores", "damaged_counts", "shipments_since", "zone_accuracy"]
