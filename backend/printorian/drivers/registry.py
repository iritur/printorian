"""Driver lookup.

Drivers are the one genuinely pluggable extension point (ADR-0009): brands are
registered by name, and nothing else in the system knows a brand exists. Adding
Elegoo SDCP later touches this file and one new module — not ``scheduling``,
not ``production``.
"""

from __future__ import annotations

from collections.abc import Callable

from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.errors import ConfigurationError
from printorian.drivers.bambu.driver import BambuDriver
from printorian.drivers.base import ConnectionInfo, PrinterDriver
from printorian.drivers.manual import ManualPrinterDriver
from printorian.drivers.mock import MockPrinterDriver

DriverFactory = Callable[[ConnectionInfo, Clock, Settings], PrinterDriver]

_REGISTRY: dict[str, DriverFactory] = {}


def register(brand: str, factory: DriverFactory) -> None:
    _REGISTRY[brand.lower()] = factory


def available() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build(brand: str, info: ConnectionInfo, clock: Clock, settings: Settings) -> PrinterDriver:
    """Construct the driver for ``brand``, or fail loudly.

    There is deliberately no fallback: an unknown brand is a configuration error,
    never a silent downgrade to a simulator.
    """
    factory = _REGISTRY.get(brand.lower())
    if factory is None:
        raise ConfigurationError("error.driver.unknown_brand", brand=brand, known=list(available()))
    return factory(info, clock, settings)


def _build_bambu(info: ConnectionInfo, clock: Clock, settings: Settings) -> PrinterDriver:
    return BambuDriver(info, clock)


def _build_manual(info: ConnectionInfo, clock: Clock, settings: Settings) -> PrinterDriver:
    return ManualPrinterDriver(info, clock)


def _build_mock(info: ConnectionInfo, clock: Clock, settings: Settings) -> PrinterDriver:
    return MockPrinterDriver(info, clock, settings)


register("bambu", _build_bambu)
register("manual", _build_manual)
register("mock", _build_mock)
