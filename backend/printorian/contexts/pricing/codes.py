"""Line item codes.

These are the contract between the engine and the clients' message catalogues, so
they are stable identifiers. Renaming one silently blanks a row in the customer's
price breakdown — treat a change here as a breaking API change.
"""

from __future__ import annotations

MATERIAL = "material.filament"
MATERIAL_PURGE = "material.purge"
#: Buying in a filament the farm does not hold. Charged once per order, not per
#: unit: one procurement covers the whole plate however many copies it makes.
MATERIAL_PROCUREMENT = "material.procurement"

MACHINE_ELECTRICITY = "machine.electricity"
MACHINE_DEPRECIATION = "machine.depreciation"

LABOR_SUPERVISION = "labor.supervision"
LABOR_SETUP = "labor.setup"
LABOR_ENGINEERING = "labor.engineering"
#: Finishes append their own code, e.g. ``postprocess.polish``.
POSTPROCESS_PREFIX = "postprocess."

LOGISTICS_PACKAGING = "logistics.packaging"
#: The base charge for reaching the destination — the zone's, once a postcode
#: names one, and the flat rate before that.
LOGISTICS_SHIPPING = "logistics.shipping"
#: The mass-dependent half of a zone tariff, emitted only where the zone actually
#: charges by weight. A separate code rather than a fatter `logistics.shipping`
#: because the customer is owed the two figures apart: one is "where", the other
#: is "how heavy", and only the second moves when they order more.
LOGISTICS_SHIPPING_WEIGHT = "logistics.shipping_weight"

OVERHEAD = "overhead.general"
RISK_FAILURE_BUFFER = "risk.failure_buffer"

ADJUSTMENT_RUSH = "adjustment.rush"
ADJUSTMENT_VOLUME_DISCOUNT = "adjustment.volume_discount"
ADJUSTMENT_CUSTOMER_DISCOUNT = "adjustment.customer_discount"

MARGIN = "margin.profit"

#: Every fixed code the engine can emit. Used by the catalogue-coverage test that
#: proves no line can reach a customer without an RU and EN label.
ALL_FIXED_CODES: tuple[str, ...] = (
    MATERIAL,
    MATERIAL_PROCUREMENT,
    MATERIAL_PURGE,
    MACHINE_ELECTRICITY,
    MACHINE_DEPRECIATION,
    LABOR_SUPERVISION,
    LABOR_SETUP,
    LABOR_ENGINEERING,
    LOGISTICS_PACKAGING,
    LOGISTICS_SHIPPING,
    LOGISTICS_SHIPPING_WEIGHT,
    OVERHEAD,
    RISK_FAILURE_BUFFER,
    ADJUSTMENT_RUSH,
    ADJUSTMENT_VOLUME_DISCOUNT,
    ADJUSTMENT_CUSTOMER_DISCOUNT,
    MARGIN,
)
