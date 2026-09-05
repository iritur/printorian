"""The collaborator that has to actually be handed over.

HANDOFF records the lesson this file exists for: the last time a service was
given a collaborator through `api/deps.py`, deleting the wiring left every test in
the suite green, because nothing asserted the assembly and every test that cared
built its own service by hand.

So these assert the assembly itself. Delete the `lots=` argument in
`get_procurement_service` and `test_the_service_is_given_an_inventory_writer`
fails at the point the hole is made, rather than a month later when a receipt
records an arrival and puts nothing on a shelf.
"""

from __future__ import annotations

import inspect

from sqlalchemy.ext.asyncio import AsyncSession

from printorian.api import deps
from printorian.api.app import create_app
from printorian.contexts.inventory import InventoryService
from printorian.contexts.procurement import ProcurementService
from printorian.core.clock import FixedClock
from printorian.core.config import Settings


def test_the_service_is_given_an_inventory_writer(
    db_session: AsyncSession, clock: FixedClock
) -> None:
    """Built the way a request builds it, and asked what it is holding.

    `ProcurementService` never touches `MaterialLot` itself — receiving goes
    through this one collaborator — so a service without it cannot put a delivery
    on a shelf at all.
    """
    service = deps.get_procurement_service(db_session, clock, deps.get_inventory_service(db_session))

    assert isinstance(service, ProcurementService)
    assert isinstance(service._lots, InventoryService)


def test_the_writer_is_injected_rather_than_built_inside_the_service() -> None:
    """A required keyword-only argument, which is what lets a test hand it a
    double and what keeps `api` and `workers` siblings — neither builds the
    other's assembly, `deps.py` does."""
    parameter = inspect.signature(ProcurementService.__init__).parameters["lots"]

    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_purchasing_routes_are_actually_mounted(settings: Settings) -> None:
    """A router written and never included is the shape of the gap this branch
    inherited: four files describing tables that existed nowhere."""
    app = create_app(settings)
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/purchasing/board" in paths
    assert "/purchasing/orders/{po_id}" in paths
    assert "/purchasing/orders/{po_id}/receive" in paths
