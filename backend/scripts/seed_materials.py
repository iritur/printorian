"""Seed a realistic filament catalogue for development.

The storefront asks the customer for a **colour** and a **material** as two
independent choices, so the catalogue has to be a matrix rather than a handful of
one-off products: every colour the shop offers should exist in every material it
offers, or the two dropdowns disagree with each other.

Stock is deliberately partial. Roughly half the matrix has lots and half does not,
because the interesting behaviour — a customer choosing something the farm has to
buy in, and seeing the procurement charge appear in the price — only shows up when
some of the catalogue is *not* on the shelf.

    cd backend && .venv/Scripts/python scripts/seed_materials.py

Idempotent: re-running updates the specs in place and tops the stock back up
rather than creating a second catalogue.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from printorian.contexts.inventory import CreateMaterialLot, CreateMaterialSpec, InventoryService
from printorian.contexts.inventory.models import MaterialLot, MaterialSpec
from printorian.core.config import Settings

#: One palette shared by every family, so "red" means the same colour whichever
#: material the customer picks.
PALETTE: list[tuple[str, str, str]] = [
    ("white", "White", "#F5F5F5"),
    ("black", "Black", "#141414"),
    ("grey", "Grey", "#8A8F98"),
    ("red", "Red", "#C0392B"),
    ("orange", "Orange", "#E67E22"),
    ("yellow", "Yellow", "#F1C40F"),
    ("green", "Green", "#27AE60"),
    ("blue", "Blue", "#2F6FED"),
]

#: family -> (density, price per gram, colours stocked today)
#:
#: The stocked list is what the farm holds; everything else in the matrix is a
#: real product it can buy, and the customer pays the procurement charge for it.
FAMILIES: dict[str, tuple[Decimal, Decimal, set[str]]] = {
    "PLA": (Decimal("1.24"), Decimal("2.40"), {"white", "black", "grey", "red", "blue"}),
    "PETG": (Decimal("1.27"), Decimal("3.60"), {"black", "clear", "red"}),
    "ABS": (Decimal("1.04"), Decimal("3.10"), {"black", "white"}),
    "TPU": (Decimal("1.21"), Decimal("5.40"), {"black"}),
}

#: Physical properties per family, and whether the family survives outdoors.
#:
#: Tensile strength and heat-deflection temperature are the **medians of that
#: family's grades** in `data/seed/material-catalog.json`, whose 142 entries carry
#: both. The seeder used to ignore that file entirely and record neither, which
#: left `GET /materials/recommend` unable to match anything: every scenario in the
#: configurator asks for a minimum tensile or HDT, and every spec answered `NULL`.
#:
#: `is_outdoor_safe` is *not* in that data and is not derivable from it — it is
#: material knowledge. ASA and ABS hold up under UV and weather, PETG tolerates it,
#: PLA does not: it softens in a hot car and goes brittle in sunlight. Stated here
#: as a constant rather than guessed per grade, because a wrong answer sends a
#: load-bearing outdoor bracket out in PLA.
FAMILY_PROPERTIES: dict[str, tuple[Decimal, Decimal, bool]] = {
    # family: (tensile MPa, HDT °C, outdoor-safe)
    "PLA": (Decimal(72), Decimal(50), False),
    "PETG": (Decimal(52), Decimal(70), True),
    "ABS": (Decimal(43), Decimal(80), True),
    "TPU": (Decimal(35), Decimal(60), False),
}

#: PETG's signature colour, which no other family offers.
PETG_CLEAR = ("clear", "Clear", "#DDEEFF")

STOCK_GRAMS = Decimal(1000)


def _properties(family: str) -> dict[str, Decimal | bool]:
    """The recommender's inputs for this family, or nothing if unknown.

    An unknown family records no properties rather than plausible ones: a spec
    that claims 50 MPa it was never measured at would be recommended for a
    load-bearing part on the strength of a default.
    """
    found = FAMILY_PROPERTIES.get(family)
    if found is None:
        return {}
    tensile, hdt, outdoor = found
    return {"tensile_mpa": tensile, "hdt_c": hdt, "is_outdoor_safe": outdoor}


def catalogue() -> list[tuple[str, str, str, str, str, Decimal, Decimal, bool]]:
    """Every product the shop offers: code, name, family, colour, hex, density, price, stocked."""
    rows = []
    for family, (density, price, stocked) in FAMILIES.items():
        palette = [*PALETTE, PETG_CLEAR] if family == "PETG" else PALETTE
        for slug, colour_name, colour_hex in palette:
            rows.append(
                (
                    f"{family.lower()}-{slug}",
                    f"{family} {colour_name}",
                    family,
                    colour_name,
                    colour_hex,
                    density,
                    # Lighter colours cost a little less; it is a plausible spread
                    # rather than every product priced identically.
                    price if slug not in {"white", "black"} else price - Decimal("0.20"),
                    slug in stocked,
                )
            )
    return rows


async def main() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    created = restocked = 0
    async with factory() as session:
        inventory = InventoryService(session)

        for code, name, family, colour, colour_hex, density, price, stocked in catalogue():
            existing = await session.scalar(select(MaterialSpec).where(MaterialSpec.code == code))
            if existing is None:
                await inventory.create_spec(
                    CreateMaterialSpec(
                        code=code,
                        name=name,
                        family=family,
                        color_name=colour,
                        color_hex=colour_hex,
                        density_g_per_cm3=density,
                        sell_price_per_gram=price,
                        is_flexible=family == "TPU",
                        **_properties(family),
                    )
                )
                created += 1
            else:
                # Re-seeding corrects a spec edited by hand — an early ABS entry
                # kept the placeholder grey rather than its real colour.
                existing.name = name
                existing.color_name = colour
                existing.color_hex = colour_hex
                existing.sell_price_per_gram = price
                existing.density_g_per_cm3 = density
                # Backfill the recommender's inputs too. Specs seeded before these
                # existed hold NULL, and a NULL tensile is a spec no scenario can
                # ever match — which looked like a broken recommender rather than
                # missing data.
                for field, value in _properties(family).items():
                    setattr(existing, field, value)

            if not stocked:
                continue

            spec = await session.scalar(select(MaterialSpec).where(MaterialSpec.code == code))
            assert spec is not None
            held = await session.scalar(select(MaterialLot).where(MaterialLot.spec_id == spec.id))
            if held is None:
                await inventory.add_lot(
                    CreateMaterialLot(
                        spec_code=code, label=f"{code}-001", initial_grams=STOCK_GRAMS, shelf="A1"
                    )
                )
                restocked += 1

        await session.commit()

    await engine.dispose()
    total = len(catalogue())
    print(f"catalogue: {total} products | created {created} | stocked {restocked}")


if __name__ == "__main__":
    asyncio.run(main())
