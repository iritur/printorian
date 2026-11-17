---
name: printorian-bambu-ftps-session-reuse
description: "Bambu file transfer is implicit FTPS on port 990 with a TLS session-reuse requirement that standard ftplib breaks on — the fix is the ntransfercmd override in tools/bambu_ftps.py; printers without writable storage raise DriverStorageError (error.driver.storage_unavailable) and must never be counted as available capacity."
metadata:
  node_type: memory
  type: project
---

**Why:** The FTPS session-reuse trap produces transfers that fail in ways
that look like network flakiness, and a printer that cannot store a plate
is not "slow capacity" — treating it as available poisons the scheduler's
denominator (ADR-0007).

**How to apply:** Never reimplement Bambu file transfer with plain
`ftplib.FTP_TLS` — use `tools/bambu_ftps.py`, whose `ntransfercmd` fix
handles the session-reuse requirement (measured against a real X1C-class
machine, vsftpd 3.0.5). `DriverStorageError` maps to
`error.driver.storage_unavailable` and the printer leaves the eligible
roster until storage is fixed.

Evidence: docs/BAMBU-LAN-PROTOCOL.md (Phase-0 spike, "X2D / PF004-P,
firmware as shipped, LAN Mode on"): implicit FTPS on 990; control channel
facts and the session-reuse trap recorded with the fix location; observed
ports 322, 990, 6000 (chamber camera). Control/telemetry is MQTT/TLS 8883
as `bblp` + access code, topic pair `device/<serial>/report|request`,
`CERT_NONE` per the protocol's own certificate story.

Related: [[printorian-silent-printer-60s]].
