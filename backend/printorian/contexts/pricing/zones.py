"""Shipping zones: what a parcel costs depending on where it is going.

Until now shipping was one flat rate for the whole world, which is a number the
farm never measured — Moscow and Vladivostok cost it wildly different amounts and
the quote said the same thing about both. A zone tariff is the farm's own answer:
a base charge, an optional per-kilogram charge, a transit time, and the postcode
prefixes that reach it.

Three properties are deliberate, and each of them is a rule the rest of the system
depends on:

* **This module is pure.** It imports the standard library and
  `printorian.core.errors`, nothing else. The tariff is a *rate*, so it travels
  inside a :class:`~printorian.contexts.pricing.rates.RateSnapshot` and is handed
  to the engine, which looks nothing up (ADR-0002). The `pricing-purity` import
  contract runs with ``allow_indirect_imports = False`` and would fail on a stray
  import rather than leaving it to a reviewer.
* **An unmatched postcode has no zone.** :func:`zone_for` returns ``None`` and
  never a nearest, first or default zone. Pricing an unknown destination as
  Moscow would be presenting a price the farm never set as one it did, which is
  exactly what CLAUDE.md §1 and ADR-0007 forbid. The caller falls back to the
  documented flat rate instead, and the customer still gets a figure.
* **The zone is found by postcode alone**, which is what the design kit's own
  footer promises («ЗОНА ОПРЕДЕЛЯЕТСЯ ПО ИНДЕКСУ»). The city is captured on the
  order and shown to a human, but it is not a matching key: two Russian cities
  share a name often enough that matching on one would silently price a parcel
  for the wrong end of the country, and there is no city list to check against.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from printorian.core.errors import ValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class ShippingZone:
    """One destination band and what the farm charges to reach it.

    ``base`` is charged once per order — one order ships once — and ``per_kg``
    rides on the mass actually printed. A zone with ``per_kg == 0`` is a flat-rate
    destination, which is how the kit draws Moscow: inside the MKAD the courier
    charges the same for a keyring and a crate.
    """

    code: str
    base: Decimal = Decimal(0)
    per_kg: Decimal = Decimal(0)
    #: Working days the carrier takes. Feeds the promise a later slice makes; it
    #: is not money and never reaches the breakdown.
    transit_days: int = 0
    #: Postcodes beginning with any of these belong to this zone. The longest
    #: match wins, so «Москва · МКАД» can carve itself out of «Россия».
    postcode_prefixes: tuple[str, ...] = ()
    #: A zone the farm has drawn but does not currently serve. Kept rather than
    #: deleted so the rows an old order was priced against still read back.
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValidationError("error.pricing.zone_code")
        for name in ("base", "per_kg"):
            if getattr(self, name) < 0:
                raise ValidationError("error.pricing.zone_negative_rate", code=self.code, rate=name)
        if self.transit_days < 0:
            raise ValidationError(
                "error.pricing.zone_transit_days", code=self.code, value=self.transit_days
            )
        for prefix in self.postcode_prefixes:
            # An empty prefix matches every postcode, so one blank cell in the
            # editor would quietly make this zone the answer for the whole world.
            # Refusing it here is the difference between a typo and a mispriced
            # month of orders.
            if not prefix.strip():
                raise ValidationError("error.pricing.zone_prefix_empty", code=self.code)


@dataclass(frozen=True, slots=True)
class ZoneTariffs:
    """The whole zone table, as one value that can be pinned to an order.

    Empty by default, and that default is load-bearing: a farm that has drawn no
    zones prices exactly as it did before zones existed. It is the same rule the
    settings context is built around — a key with no row is the code default —
    and it is what makes every pricing test written before this change a
    regression guard for it.

    Declaration order is kept rather than sorted, because it is the order the
    console draws the rows in and the farm chose it. Matching does not depend on
    it: the longest prefix wins wherever the row sits.
    """

    zones: tuple[ShippingZone, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for zone in self.zones:
            if zone.code in seen:
                # Two rows claiming one code cannot both be archived under it, and
                # whichever the lookup happened to reach first would be an accident
                # of ordering rather than a decision.
                raise ValidationError("error.pricing.duplicate_zone", code=zone.code)
            seen.add(zone.code)


def zone_for(tariffs: ZoneTariffs, postcode: str) -> ShippingZone | None:
    """The zone a postcode falls in, or ``None`` when the farm has not said.

    ``None`` is the honest answer for three different situations and the caller
    treats them alike: the farm has drawn no zones, the customer has not typed a
    postcode yet, or the postcode matches nothing anybody drew. In each the price
    falls back to the flat rate rather than to a guess.

    The **longest** matching prefix wins so that a narrow zone can sit inside a
    broad one — «1» reaching all of central Russia and «101» carving Moscow out of
    it. Taking the first match instead would make the answer depend on the row
    order in a settings table, which is not where a price should come from.
    """
    normalized = postcode.strip().upper()
    if not normalized:
        return None
    best: ShippingZone | None = None
    best_length = -1
    for zone in tariffs.zones:
        if not zone.enabled:
            continue
        for prefix in zone.postcode_prefixes:
            candidate = prefix.strip().upper()
            if normalized.startswith(candidate) and len(candidate) > best_length:
                best = zone
                best_length = len(candidate)
    return best


__all__ = ["ShippingZone", "ZoneTariffs", "zone_for"]
