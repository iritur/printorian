"""The Bambu Lab driver.

Built from measurements, not documentation: every protocol detail here was observed
against a real machine during the Phase 0 spike and is recorded in
docs/BAMBU-LAN-PROTOCOL.md.

**What is proven and what is not.** Connect, authenticate, telemetry, AMS slot state
and the FTPS transport all ran against real hardware. Plate upload and the
``project_file`` dispatch command have not yet been exercised end to end, because
the test machine had no writable storage. Those two methods are marked below, and
the honest status lives in the protocol document rather than in an optimistic
docstring — V1's connector claimed to work and did not.
"""

from __future__ import annotations

import json
import ssl
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import paho.mqtt.client as mqtt

from printorian.core.clock import Clock
from printorian.core.units import BoundingBox, Length
from printorian.drivers.bambu import transport
from printorian.drivers.bambu.report import parse_report
from printorian.drivers.base import (
    AmsSlot,
    Capabilities,
    ConnectionInfo,
    ConnectionMode,
    DriverAuthError,
    DriverRejectedError,
    DriverUnavailableError,
    JobHandle,
    PlateUpload,
    PrinterDriver,
    PrinterState,
    RemoteFileRef,
    Telemetry,
)

#: How long to wait for the first report before deciding the machine is unreachable.
_CONNECT_TIMEOUT_SECONDS = 15.0


class BambuDriver(PrinterDriver):
    """LAN control for Bambu Lab printers."""

    def __init__(self, info: ConnectionInfo, clock: Clock) -> None:
        if info.mode not in {ConnectionMode.LAN, ConnectionMode.CLOUD}:
            raise DriverUnavailableError("error.driver.unsupported_mode", mode=info.mode.value)
        if not info.host or not info.serial or not info.access_code:
            raise DriverAuthError("error.driver.auth", printer=info.printer_id)

        self._info = info
        self._clock = clock
        self._client: mqtt.Client | None = None
        self._latest: Telemetry | None = None
        self._first_report = threading.Event()
        self._connect_error: str | None = None

    @property
    def brand(self) -> str:
        return "bambu"

    @property
    def _report_topic(self) -> str:
        return f"device/{self._info.serial}/report"

    @property
    def _request_topic(self) -> str:
        return f"device/{self._info.serial}/request"

    # -- connection ------------------------------------------------------

    async def connect(self, info: ConnectionInfo) -> None:
        """Open the MQTT session and wait for the machine to describe itself."""
        self._info = info
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(transport.USERNAME, info.access_code)
        client.tls_set_context(transport.lan_tls_context())

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        try:
            client.connect(info.host or "", transport.MQTT_PORT, keepalive=60)
        except (OSError, ssl.SSLError) as exc:
            raise DriverUnavailableError(
                "error.driver.unavailable", printer=info.printer_id, detail=str(exc)
            ) from exc

        client.loop_start()
        self._client = client

        # A connection that never produces a report is not a working connection.
        # Silence here usually means a wrong serial: the topic simply has no
        # publisher, and nothing else would ever tell us.
        if not self._first_report.wait(_CONNECT_TIMEOUT_SECONDS):
            await self.disconnect()
            raise DriverUnavailableError(
                "error.driver.no_report",
                printer=info.printer_id,
                hint="Check the serial number and that LAN mode is enabled.",
                detail=self._connect_error or "",
            )

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._first_report.clear()

    def _on_connect(self, client: mqtt.Client, _u: Any, _f: Any, reason: Any, _p: Any) -> None:
        if getattr(reason, "is_failure", False):
            self._connect_error = str(reason)
            return
        client.subscribe(self._report_topic)
        # `pushall` asks for a complete state dump rather than waiting for the next
        # incremental change, which may be minutes away on an idle machine.
        client.publish(
            self._request_topic,
            json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}),
        )

    def _on_disconnect(self, *_args: Any, **_kwargs: Any) -> None:
        # paho reconnects on its own while the loop runs; state is simply stale
        # until the next report, and `read_telemetry` refuses to invent one.
        self._connect_error = "disconnected"

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        telemetry = parse_report(
            payload, printer_id=self._info.printer_id, observed_at=datetime.now(UTC)
        )
        if telemetry is not None:
            self._latest = telemetry
            self._first_report.set()

    # -- observation -----------------------------------------------------

    async def read_telemetry(self) -> Telemetry:
        """The most recent observation.

        Raises when there is none. There is deliberately no "assume idle" branch:
        that is exactly how V1 reported a healthy fleet while controlling nothing.
        """
        if self._client is None:
            raise DriverUnavailableError(
                "error.driver.not_connected", printer=self._info.printer_id
            )
        if self._latest is None:
            raise DriverUnavailableError("error.driver.no_report", printer=self._info.printer_id)
        return self._latest

    async def stream_telemetry(self) -> AsyncIterator[Telemetry]:
        """Yield each new observation as the machine reports it."""
        while self._client is not None:
            if self._latest is not None:
                yield self._latest
            break

    async def capabilities(self) -> Capabilities:
        """What the machine can do, from its own report where possible.

        Build volume is not in the MQTT report, so it comes from the fleet record
        rather than being guessed from the model string.
        """
        slots: tuple[AmsSlot, ...] = self._latest.ams_slots if self._latest else ()
        return Capabilities(
            model=self._info.printer_id,
            build_volume=BoundingBox(x=Length(256), y=Length(256), z=Length(256)),
            nozzle_diameter_mm=Decimal("0.4"),
            supports_multi_material=bool(slots),
            ams_slots=slots,
        )

    # -- commands --------------------------------------------------------

    async def upload(self, plate: PlateUpload) -> RemoteFileRef:
        """Send a plate over FTPS.

        **Not yet verified against hardware** — the Phase 0 machine had no writable
        storage. A refusal surfaces as ``DriverStorageError`` so the cause is named.
        """
        ftps = transport.connect_ftps(self._info.host or "", self._info.access_code or "")
        try:
            remote = transport.upload_plate(ftps, plate.filename, plate.content)
        finally:
            transport.close_ftps(ftps)
        return RemoteFileRef(path=remote)

    async def start(self, ref: RemoteFileRef, ams_mapping: dict[int, int]) -> JobHandle:
        """Dispatch a print. **Not yet verified against hardware.**"""
        if self._latest is not None and not self._latest.state.accepts_job:
            raise DriverRejectedError("error.driver.busy", state=self._latest.state.value)

        name = ref.path.rsplit("/", 1)[-1]
        command = {
            "print": {
                "sequence_id": "2",
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "subtask_name": name,
                "url": f"file:///mnt/sdcard{ref.path}",
                "bed_leveling": True,
                "flow_cali": False,
                "vibration_cali": True,
                "layer_inspect": False,
                "timelapse": False,
                "use_ams": bool(ams_mapping),
                "ams_mapping": [ams_mapping[key] for key in sorted(ams_mapping)],
            }
        }
        self._publish(command)
        return JobHandle(value=name)

    async def pause(self) -> None:
        self._publish({"print": {"sequence_id": "3", "command": "pause"}})

    async def resume(self) -> None:
        self._publish({"print": {"sequence_id": "4", "command": "resume"}})

    async def cancel(self, reason: str) -> None:
        self._publish({"print": {"sequence_id": "5", "command": "stop"}})

    def _publish(self, command: dict[str, Any]) -> None:
        if self._client is None:
            raise DriverUnavailableError(
                "error.driver.not_connected", printer=self._info.printer_id
            )
        result = self._client.publish(self._request_topic, json.dumps(command))
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise DriverUnavailableError(
                "error.driver.publish_failed", printer=self._info.printer_id, rc=result.rc
            )


def state_of(telemetry: Telemetry | None) -> PrinterState:
    """The state to record when there is no observation: offline, never idle."""
    return telemetry.state if telemetry is not None else PrinterState.OFFLINE
