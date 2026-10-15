"""Helpers shared by the driver-pool tests. No fixtures here — the `_support` rule."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from printorian.contexts.fleet import ConnectionMode, CreatePrinter, FleetService
from printorian.contexts.fleet.models import Printer
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.workers.drivers import DriverPool

KEY = "a-development-secret-key-not-for-production"


class FakeDriver:
    """Counts what the pool does to it."""

    def __init__(self) -> None:
        self.connects = 0
        self.disconnects = 0

    @property
    def brand(self) -> str:
        return "fake"

    async def connect(self, info: object) -> None:
        self.connects += 1

    async def disconnect(self) -> None:
        self.disconnects += 1


async def a_printer(
    fleet: FleetService, *, name: str = "P1S-01", host: str = "10.0.0.5"
) -> Printer:
    view = await fleet.register(
        CreatePrinter(
            name=name,
            brand="fake",
            model="P1S",
            connection_mode=ConnectionMode.LAN,
            host=host,
            serial="SERIAL-1",
            width_mm=Decimal(256),
            depth_mm=Decimal(256),
            height_mm=Decimal(256),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=True,
        )
    )
    return await fleet._load(view.id)


def pool_with(
    monkeypatch: pytest.MonkeyPatch, driver: object, clock: FixedClock, **options: object
) -> DriverPool:
    """A pool whose registry hands back the driver under test, for every printer."""
    return pool_building(monkeypatch, lambda: driver, clock, **options)


def pool_building(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], object],
    clock: FixedClock,
    **options: object,
) -> DriverPool:
    """A pool whose registry builds a fresh driver per connection attempt."""
    from printorian.workers import drivers as module

    monkeypatch.setattr(module.driver_registry, "build", lambda *a, **k: factory())
    return DriverPool(clock, Settings(secret_key=KEY), **options)  # type: ignore[arg-type]
