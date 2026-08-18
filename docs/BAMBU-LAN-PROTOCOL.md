# Bambu LAN protocol — spike findings

Measured against a real machine (X2D / PF004-P, firmware as shipped, LAN Mode on)
during Phase 0. This is the specification the Phase 3 driver is built from, and the
reason it will not be guesswork.

**Status: telemetry and control proven. File transfer proven at the transport level;
the write itself is pending storage in the test machine.**

---

## 1. Discovery — UDP 1990 / 2021

Bambu printers broadcast on these ports. In our environment nothing was heard in 45 s
(port 2021 could not even be bound — Windows reserves it), so **discovery is optional
and must never be a prerequisite**. A printer is perfectly usable when its IP is known.

Phase 3: treat discovery as a convenience for adding printers, never as the source of
truth for whether a printer exists.

## 2. Telemetry and control — MQTT over TLS, port 8883 ✅ **proven**

* username `bblp`, password is the **LAN access code**, self-signed certificate
  (`CERT_NONE` — the access code is the authentication factor)
* subscribe `device/<serial>/report`, publish to `device/<serial>/request`
* `{"pushing": {"sequence_id": "1", "command": "pushall"}}` triggers a full state dump

Fields that map onto `drivers.base.Telemetry`:

| MQTT field | Meaning |
|---|---|
| `gcode_state` | `FINISH`, `RUNNING`, `PAUSE`, `FAILED`, `IDLE` |
| `mc_percent` | progress 0–100 |
| `layer_num` / `total_layer_num` | layer counters |
| `mc_remaining_time` | minutes remaining |
| `nozzle_temper` / `bed_temper` | temperatures |
| `ams.ams[].tray[]` | per-slot `tray_type` and `tray_color` |

### Three things real hardware corrected

1. **`FINISH` is not "available".** The machine reported 100%, 785/785 layers, nozzle
   cooled to 29 °C — numerically indistinguishable from idle, with the finished part
   still on the bed. `PrinterState.accepts_job` is therefore true for `IDLE` only.
   `FINISHED` clears to `IDLE` when a human removes the part.
2. **`tray_color` is 8-char RGBA** (`FFFFFFFF`), not `#RRGGBB`. The driver must convert.
3. **`subtask_name` is the print profile**, not a filename — the observed value was
   `"0.2mm layer, 2 walls, 15% infill"`. It cannot be used to correlate a running
   print back to a job; Phase 3 needs its own correlation key.

## 3. File transfer — implicit FTPS, port 990

Transport **proven**: TLS handshake, `bblp` login, `PROT P`, and data channels all work.
The server is **vsftpd 3.0.5**, `SYST` = `UNIX Type: L8`, features include `EPSV`,
`PASV`, `SIZE`, `MDTM`, `REST STREAM`, `TVFS`, `UTF8`. Login lands at `/`.

### The trap: TLS session reuse

vsftpd defaults to `require_ssl_reuse=YES` — the **data** connection must resume the
**control** connection's TLS session. Python's `ftplib` negotiates a fresh session, so
without a fix every `LIST` returns empty and every `STOR` fails with
`553 Could not create file`, a message that points at file permissions when the cause
is the TLS handshake.

The fix, in `tools/bambu_ftps.py` and required in the Phase 3 driver:

```python
def ntransfercmd(self, cmd, rest=None):
    conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
    if self._prot_p:
        conn = self.context.wrap_socket(
            conn, server_hostname=self.host, session=self.sock.session
        )
    return conn, size
```

Port 990 is **implicit** TLS — the socket is wrapped from the first byte, which stdlib
`FTP_TLS` does not do. Both overrides are needed.

### Outstanding: no storage on the test machine

With no card inserted, the filesystem is an empty read-only root:

* `LIST /` → 0 lines, no error
* `CWD` into `/cache`, `/model`, `/data`, `/sdcard`, `/mnt`, `/usb`, … → all
  `550 Failed to change directory`
* `STOR` anywhere → `553 Could not create file`

This is expected: on these machines FTP serves the removable storage. The remaining
verification is a single `upload` run with a card or USB drive inserted.

**Product consequence, already encoded:** a printer with no writable storage connects,
authenticates and reports telemetry perfectly, then fails only at dispatch. That is
`DriverStorageError` (`error.driver.storage_unavailable`) — distinct from "rejected"
and "unreachable", because the remedy is physical and an operator needs to be told
*which machine to walk to*. Such a printer must never be counted as available capacity.

## 4. Other observed ports

A capture during a Bambu Studio session showed **322**, **990** and **6000** open to the
printer. 6000 is the chamber camera stream. 322 is a Bambu control channel we have not
characterised — worth investigating in Phase 3 if 990 ever proves insufficient.

---

## What Phase 3 inherits

| Capability | Status |
|---|---|
| Connect + authenticate | proven |
| Live telemetry, progress, temperatures | proven |
| AMS slot → material mapping | proven |
| Normalized state machine | proven, and corrected by real data |
| FTPS transport incl. TLS session reuse | proven |
| Plate upload | pending storage in the test machine |
| `project_file` dispatch command | untested |

No evidence was found of firmware-level blocking of third-party LAN control. Every
failure encountered was mundane and explained: a Windows port reservation, a vsftpd TLS
requirement, and an absent storage device.
