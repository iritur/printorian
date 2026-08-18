"""Bambu Lab LAN protocol spike - run this against real hardware before Phase 3.

This is the Phase 0 gating experiment (ROADMAP). It is deliberately standalone: no
Printorian imports, no database, nothing to configure. If it works, the Phase 3
driver is a known quantity. If it does not, "auto-dispatch" becomes "auto-assign +
operator confirms" and that is a scope change we need to know about in week one,
not month four.

V1 never ran this experiment. Its connector issued ``GET /api/v1/status``, an
endpoint that does not exist, and silently returned fabricated data.

What the protocol actually is, in LAN mode:

* discovery: SSDP-ish UDP broadcast on ports 1990/2021
* telemetry + control: MQTT over TLS on 8883, username ``bblp``, password is the
  printer's LAN access code, self-signed certificate
* file transfer: *implicit* FTPS on 990, same credentials, plates go to ``/cache/``

This docstring is printed as ``--help`` on a Windows console, so it stays ASCII.

Install the one dependency, then run::

    pip install paho-mqtt
    python tools/bambu_spike.py discover
    python tools/bambu_spike.py printers
    python tools/bambu_spike.py status --printer p1s-01
    python tools/bambu_spike.py upload --printer p1s-01 --file plate.3mf
    python tools/bambu_spike.py print  --printer p1s-01 --file plate.3mf

Names come from printers.local.toml (see tools/printer_registry.py); --host,
--serial and --code still work and override the file.

Find the access code on the printer: Settings > Network > LAN Only Mode.

Exit code 0 means that step of the protocol works.
"""

from __future__ import annotations

import argparse
import contextlib
import ftplib
import json
import socket
import ssl
import sys
import time
from pathlib import Path

import bambu_ftps
import bambu_mqtt
import printer_registry

DISCOVERY_PORTS = (1990, 2021)


# --------------------------------------------------------------- discovery


def discover(timeout: float) -> int:
    """Listen for the broadcast Bambu printers emit on the local network."""
    print(f"listening on UDP {DISCOVERY_PORTS} for {timeout:.0f}s ...")
    sockets = []
    for port in DISCOVERY_PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", port))
        except OSError as exc:
            print(f"  cannot bind {port}: {exc}")
            sock.close()
            continue
        sock.settimeout(0.5)
        sockets.append(sock)

    if not sockets:
        print("no sockets bound — is another process holding these ports?")
        return 1

    seen: dict[str, str] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for sock in sockets:
            try:
                payload, (address, _) = sock.recvfrom(4096)
            except (TimeoutError, OSError):
                continue
            text = payload.decode("utf-8", errors="replace")
            if address not in seen:
                seen[address] = text
                print(f"\n  {address}\n    {text.strip()[:400]}")

    for sock in sockets:
        sock.close()

    print(f"\nfound {len(seen)} device(s)")
    return 0 if seen else 1


# -------------------------------------------------------------------- ftps


def survey(host: str, code: str) -> int:
    """List the printer's storage, so a refused upload can be explained."""
    print(f"connecting to {host}:{bambu_ftps.FTPS_PORT} (implicit TLS) ...")
    try:
        ftps = bambu_ftps.connect(host, code)
    except (OSError, *ftplib.all_errors) as exc:
        print(f"FAILED to connect: {exc}")
        return 1

    try:
        for label, value in bambu_ftps.server_info(ftps).items():
            print(f"  {label:<10} {value}")

        found = bambu_ftps.survey(ftps)
        readable = 0
        for path, (entries, error) in found.items():
            if error:
                print(f"\n  {path:<12} ERROR: {error}")
                continue
            readable += 1
            print(f"\n  {path:<12} {len(entries)} entries")
            for entry in entries[:20]:
                print(f"      {entry}")

        # Listing tells you nothing about writability, which is what an upload
        # actually needs — every path above listed cleanly and STOR still failed.
        print("\nwritability:")
        writable = 0
        for path in ("/", "/cache", "/data", "/model"):
            error = bambu_ftps.probe_writable(ftps, path)
            if error is None:
                writable += 1
            print(f"  {path:<10} {'WRITABLE' if error is None else error}")

        if readable == 0:
            print("\nEvery listing failed - check the data channel (TLS session reuse).")
            return 1
        if writable == 0:
            print("\nReadable but nothing is writable: no storage device is mounted.")
            print("If Bambu Studio can still send prints, it is not using FTPS here -")
            print("watch which port it opens and we will follow that instead.")
            return 1
        return 0
    finally:
        with contextlib.suppress(*ftplib.all_errors):
            ftps.quit()


def upload(host: str, code: str, path: Path) -> int:
    """Push a plate to the printer, trying each plausible directory."""
    if not path.is_file():
        print(f"no such file: {path}")
        return 1

    print(f"connecting to {host}:{bambu_ftps.FTPS_PORT} (implicit TLS) ...")
    try:
        ftps = bambu_ftps.connect(host, code)
    except (OSError, *ftplib.all_errors) as exc:
        print(f"FAILED to connect: {exc}")
        return 1

    try:
        print(f"uploading {path.name} ({path.stat().st_size} bytes)")
        remote, attempts = bambu_ftps.upload_anywhere(ftps, path)
        for line in attempts:
            print(line)

        if remote is None:
            print("\nEvery candidate directory refused the file.")
            print("553 usually means no microSD card is mounted, or it is full or")
            print("write-protected. Run `bambu_spike.py ls` to see what is readable.")
            return 1

        listing, _ = bambu_ftps.listdir(ftps, remote.rsplit("/", 1)[0] or "/")
        print(f"\nuploaded to {remote}; directory now has {len(listing)} entries")
        return 0
    finally:
        with contextlib.suppress(*ftplib.all_errors):
            ftps.quit()


# ------------------------------------------------------------------- print


def start_print(host: str, serial: str, code: str, path: Path, use_ams: bool) -> int:
    """The whole point of the spike: upload a plate and make the machine print it."""
    if upload(host, code, path) != 0:
        return 1

    client = bambu_mqtt.client(host, code)
    request_topic = f"device/{serial}/request"

    command = {
        "print": {
            "sequence_id": "2",
            "command": "project_file",
            "param": "Metadata/plate_1.gcode",
            "subtask_name": path.stem,
            "url": f"file:///mnt/sdcard/cache/{path.name}",
            "timelapse": False,
            "bed_leveling": True,
            "flow_cali": False,
            "vibration_cali": True,
            "layer_inspect": False,
            "use_ams": use_ams,
        }
    }

    print(f"\nconnecting to {host}:{bambu_mqtt.MQTT_PORT} to dispatch ...")
    try:
        client.connect(host, bambu_mqtt.MQTT_PORT, keepalive=60)
    except (TimeoutError, OSError, ssl.SSLError) as exc:
        print(f"FAILED to connect: {exc}")
        return 1

    client.loop_start()
    time.sleep(1)
    result = client.publish(request_topic, json.dumps(command))
    result.wait_for_publish(timeout=10)
    print(f"published project_file command (rc={result.rc})")
    time.sleep(3)
    client.loop_stop()
    client.disconnect()

    print("\nWatch the printer. If it starts, LAN dispatch works and Phase 3 is unblocked.")
    return 0


# --------------------------------------------------------------------- cli


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover", help="find printers via UDP broadcast")
    p_discover.add_argument("--timeout", type=float, default=10)

    def add_target(sub_parser: argparse.ArgumentParser) -> None:
        """Credentials come from printers.local.toml, or from explicit flags."""
        sub_parser.add_argument("--printer", help="name from printers.local.toml")
        sub_parser.add_argument("--host")
        sub_parser.add_argument("--serial")
        sub_parser.add_argument("--code", help="LAN access code")

    sub.add_parser("printers", help="list registered printers (never shows codes)")

    p_status = sub.add_parser("status", help="connect over MQTT and dump state")
    add_target(p_status)
    p_status.add_argument("--timeout", type=float, default=10)

    p_ls = sub.add_parser("ls", help="list printer storage over FTPS (diagnostic)")
    add_target(p_ls)

    p_upload = sub.add_parser("upload", help="send a 3MF to the printer over implicit FTPS")
    add_target(p_upload)
    p_upload.add_argument("--file", required=True, type=Path)

    p_print = sub.add_parser("print", help="upload a plate and start printing it")
    add_target(p_print)
    p_print.add_argument("--file", required=True, type=Path)
    p_print.add_argument("--no-ams", action="store_true")

    args = parser.parse_args()

    if args.command == "discover":
        return discover(args.timeout)
    if args.command == "printers":
        print(printer_registry.describe())
        return 0

    try:
        target = printer_registry.resolve(
            args.printer, host=args.host, serial=args.serial, access_code=args.code
        )
    except printer_registry.RegistryError as exc:
        print(exc)
        return 2

    print(f"target: {target.name} ({target.host})")
    if args.command == "status":
        return bambu_mqtt.status(target.host, target.serial, target.access_code, args.timeout)
    if args.command == "ls":
        return survey(target.host, target.access_code)
    if args.command == "upload":
        return upload(target.host, target.access_code, args.file)
    if args.command == "print":
        return start_print(
            target.host, target.serial, target.access_code, args.file, not args.no_ams
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
