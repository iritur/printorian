"""The driver contract, run against a real Bambu printer.

**This is the thing that has never been proven.** Phase 4's exit criterion — a paid
order reaching a printer with no human action — was demonstrated with the `mock`
driver, and `tools/bambu_spike.py` proved the *protocol* in standalone code that
imports nothing from Printorian. Between those two sits the part nobody has run:
`printorian.drivers.bambu`, the driver the farm actually dispatches through,
talking to a machine.

So this file runs the product's driver against hardware and holds it to the same
contract `test_driver_contract.py` holds the mock to. Passing here is what turns
"the protocol works and the code looks right" into evidence.

## Running it

Credentials come from `backend/printers.local.toml`, which is git-ignored and is
the same registry the spike tools read (`tools/printer_registry.py`). Without it
every test here **skips**, so CI is unaffected and nobody needs a printer to work
on the rest of the system.

    cd backend
    export PRINTORIAN_HARDWARE=p1s-01
    ./.venv/Scripts/python.exe -m pytest tests/contract/ -m hardware -v

## Two tiers, and why

Everything below the divider **moves the machine**. `upload` writes to the
printer's storage and `start` begins a physical print — on a real bed, with real
filament, in a room somebody may not be standing in. Those are gated behind a
second, explicit opt-in:

    PRINTORIAN_HARDWARE=p1s-01 PRINTORIAN_HARDWARE_PRINT=yes ... -m pytest ...

Read-only conformance is the useful default: it is safe to run unattended, it can
run on a printer that is mid-job, and it is where most of the driver's surface
lives. Making the destructive half opt-in is not caution for its own sake — a test
suite that can start a print by accident is one nobody will run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from printorian.core.clock import SystemClock
from printorian.drivers.bambu import BambuDriver
from printorian.drivers.base import (
    ConnectionInfo,
    ConnectionMode,
    DriverError,
    PrinterState,
    Telemetry,
)

pytestmark = pytest.mark.hardware

#: Which printer from the registry, by name. Absent means "no hardware here".
PRINTER_ENV = "PRINTORIAN_HARDWARE"
#: Explicit consent to physically move the machine.
PRINT_ENV = "PRINTORIAN_HARDWARE_PRINT"


def _credentials():
    """The named printer's credentials, or a skip."""
    name = os.environ.get(PRINTER_ENV, "").strip()
    if not name:
        pytest.skip(f"set {PRINTER_ENV}=<printer> to run against real hardware")

    # Imported here rather than at module scope: `tools/` is not a package the
    # test suite imports from elsewhere, and a missing registry should skip these
    # tests rather than break collection for the whole suite.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools import printer_registry

    try:
        return printer_registry.resolve(name)
    except printer_registry.RegistryError as exc:
        pytest.skip(f"{PRINTER_ENV}={name} but the registry cannot supply it: {exc}")


@pytest.fixture
async def machine():
    """A connected driver, disconnected afterwards whatever the test did.

    The teardown matters more here than in an ordinary fixture: a leaked MQTT
    session holds a slot on the printer, and Bambu machines allow few of them.
    Leave enough of those behind and the *next* run fails for a reason that has
    nothing to do with the code.
    """
    credentials = _credentials()
    driver = BambuDriver(
        ConnectionInfo(
            printer_id=credentials.name,
            mode=ConnectionMode.LAN,
            host=credentials.host,
            serial=credentials.serial,
            access_code=credentials.access_code,
        ),
        SystemClock(),
    )
    await driver.connect(
        ConnectionInfo(
            printer_id=credentials.name,
            mode=ConnectionMode.LAN,
            host=credentials.host,
            serial=credentials.serial,
            access_code=credentials.access_code,
        )
    )
    try:
        yield driver
    finally:
        await driver.disconnect()


# ---------------------------------------------------------------- read-only


async def test_the_driver_connects_to_a_real_machine(machine: BambuDriver) -> None:
    """The first thing that has never happened in this repository.

    If this fails, nothing below it matters and the failure message is the most
    valuable output of the whole exercise — it is the difference between "MQTT
    refused the credentials", "the TLS handshake failed" and "the host is not
    there", and those are three different problems.
    """
    assert machine.brand


async def test_it_reports_capabilities_the_scheduler_can_plan_against(
    machine: BambuDriver,
) -> None:
    """Build volume and nozzle are what `can_take` filters on.

    A driver that connects but reports a build volume of zero would pass every
    mock test and make the farm believe no printer can take any job.
    """
    capabilities = await machine.capabilities()

    assert capabilities.build_volume_mm.x.millimetres > 0
    assert capabilities.build_volume_mm.y.millimetres > 0
    assert capabilities.build_volume_mm.z.millimetres > 0


async def test_telemetry_is_a_real_reading_and_not_a_plausible_one(
    machine: BambuDriver,
) -> None:
    """ADR-0007, checked against the one source that can actually falsify it.

    V1's connector called an endpoint that did not exist and returned fabricated
    data, and every screen believed it. What is asserted here is not a particular
    temperature — the bench printer's is whatever the room is — but that the
    values are *shaped like a reading*: a state the farm models, and temperatures
    that are either absent or physically possible.
    """
    telemetry: Telemetry = await machine.read_telemetry()

    assert isinstance(telemetry.state, PrinterState)
    for reading in (telemetry.nozzle_temp_c, telemetry.bed_temp_c):
        if reading is not None:
            # A machine at rest reads room temperature; one printing reads far
            # more. Both are inside this, and a fabricated zero is not.
            assert 0 < reading < 400


async def test_two_reads_do_not_contradict_each_other(machine: BambuDriver) -> None:
    """The driver keeps the latest report; reading twice must not invent movement.

    A driver that fabricates on a cache miss shows up here as a machine that
    changed state between two reads a second apart.
    """
    first = await machine.read_telemetry()
    second = await machine.read_telemetry()

    if first.state is not second.state:
        # Legitimate if the machine genuinely moved — but on a bench printer at
        # rest it is the signal worth failing on, so it is reported rather than
        # silently tolerated.
        pytest.fail(
            f"state changed between two immediate reads: {first.state} -> {second.state}. "
            "Real if the machine is mid-job; a fabrication otherwise."
        )


async def test_wrong_credentials_are_refused_rather_than_simulated(
    machine: BambuDriver,
) -> None:
    """The rule ADR-0007 exists for, proved against the real handshake.

    A wrong access code must raise. The failure mode this forbids is the one V1
    shipped: a connector that cannot authenticate, returns something anyway, and
    leaves the farm running on numbers no machine produced.
    """
    credentials = _credentials()
    wrong = BambuDriver(
        ConnectionInfo(
            printer_id=credentials.name,
            mode=ConnectionMode.LAN,
            host=credentials.host,
            serial=credentials.serial,
            access_code="00000000",
        ),
        SystemClock(),
    )

    with pytest.raises(DriverError):
        await wrong.connect(
            ConnectionInfo(
                printer_id=credentials.name,
                mode=ConnectionMode.LAN,
                host=credentials.host,
                serial=credentials.serial,
                access_code="00000000",
            )
        )
        await wrong.read_telemetry()


# ---------------------------------------------------------------- moves the machine
#
# Everything past here physically prints. Opt in deliberately.


def _printing_allowed() -> None:
    if os.environ.get(PRINT_ENV, "").strip().lower() not in {"yes", "1", "true"}:
        pytest.skip(f"set {PRINT_ENV}=yes to allow tests that physically move the printer")


async def test_a_plate_can_be_uploaded(machine: BambuDriver) -> None:
    """FTPS to the printer's storage — the half of dispatch that is not MQTT.

    Uploading is separated from starting on purpose: this can be run on a machine
    that is busy, it is reversible, and if dispatch is going to fail on a real
    farm this is the more likely half. Port 990 is *implicit* TLS and the writable
    root is the microSD; both are recorded in `tools/bambu_ftps.py` because both
    cost time to discover.
    """
    _printing_allowed()
    pytest.skip(
        "Not yet written: needs a known-good 3MF fixture sliced for this machine. "
        "See docs/RUNBOOK-FIRST-PRINT.md step 4 — the plate has to come from the "
        "farm's own prep chain for the test to prove anything about dispatch."
    )
