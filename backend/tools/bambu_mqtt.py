"""MQTT telemetry and command channel for a Bambu printer.

Split out of ``bambu_spike.py`` to keep that file under the 400-line gate.

LAN mode: TLS on port 8883, username ``bblp``, password is the printer's LAN access
code, self-signed certificate. Telemetry arrives on ``device/<serial>/report``;
commands go to ``device/<serial>/request``.
"""

from __future__ import annotations

import json
import ssl
import time
from typing import Any

MQTT_PORT = 8883
USERNAME = "bblp"


def client(host: str, code: str) -> Any:
    try:
        # Imported lazily: the spike is optional tooling, so paho is not a
        # dependency of the backend proper.
        import paho.mqtt.client as mqtt  # noqa: PLC0415
    except ImportError:
        print("paho-mqtt is required:  pip install paho-mqtt")
        raise SystemExit(2) from None

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(USERNAME, code)

    # The printer presents a self-signed certificate. Verification is disabled
    # deliberately for LAN mode; the access code is the authentication factor.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(context)
    return client


def status(host: str, serial: str, code: str, timeout: float) -> int:
    """Connect, ask for a full state push, and print what comes back."""
    mqtt_client = client(host, code)
    report_topic = f"device/{serial}/report"
    request_topic = f"device/{serial}/request"
    received: list[dict[str, Any]] = []

    def on_connect(client: Any, userdata: Any, flags: Any, reason: Any, props: Any) -> None:
        print(f"connected: {reason}")
        client.subscribe(report_topic)
        client.publish(
            request_topic,
            json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}),
        )
        print(f"subscribed {report_topic}, requested pushall")

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload)
        except json.JSONDecodeError:
            print(f"  non-JSON payload ({len(message.payload)} bytes)")
            return
        received.append(payload)
        _summarize(payload)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    print(f"connecting to {host}:{MQTT_PORT} as {USERNAME} ...")
    try:
        mqtt_client.connect(host, MQTT_PORT, keepalive=60)
    except (TimeoutError, OSError, ssl.SSLError) as exc:
        print(f"FAILED to connect: {exc}")
        return 1

    mqtt_client.loop_start()
    time.sleep(timeout)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()

    if not received:
        print("\nno messages received — wrong serial, wrong access code, or LAN mode is off")
        return 1
    print(f"\nreceived {len(received)} message(s) — MQTT telemetry WORKS")
    return 0


def _summarize(payload: dict[str, Any]) -> None:
    """Print the fields the Phase 3 driver will actually map onto Telemetry."""
    printer = payload.get("print")
    if not isinstance(printer, dict):
        print(f"  keys: {list(payload)}")
        return

    interesting = {
        "gcode_state": printer.get("gcode_state"),
        "percent": printer.get("mc_percent"),
        "layer": printer.get("layer_num"),
        "total_layers": printer.get("total_layer_num"),
        "remaining_min": printer.get("mc_remaining_time"),
        "nozzle_temp": printer.get("nozzle_temper"),
        "bed_temp": printer.get("bed_temper"),
        "file": printer.get("subtask_name"),
    }
    shown = {k: v for k, v in interesting.items() if v is not None}
    if shown:
        print(f"  state: {shown}")

    ams = printer.get("ams")
    if isinstance(ams, dict):
        for unit in ams.get("ams", []):
            for tray in unit.get("tray", []):
                if tray.get("tray_type"):
                    print(
                        f"    AMS unit {unit.get('id')} slot {tray.get('id')}: "
                        f"{tray.get('tray_type')} {tray.get('tray_color')}"
                    )
