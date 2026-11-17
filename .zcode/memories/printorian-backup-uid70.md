---
name: printorian-backup-uid70
description: "The uid-70/backup-init container misconfiguration produced 1,385 consecutive silent backup failures and 23 GB of un-archived WAL before anyone looked; the daily check that catches it is pg_stat_archiver (failed_count must be 0 and archived_count must climb), and backups run on the host with PRINTORIAN_DB_PORT=5433."
metadata:
  node_type: memory
  type: project
---

**Why:** Backup failure is the quietest failure class in the system — every
other signal stays green while recovery point silently degrades to "none".
1,385 silent failures is what "quiet" looks like in practice.

**How to apply:** Treat `pg_stat_archiver` as the backup heartbeat
(`failed_count = 0`, `archived_count` climbing); the alert rules
`BackupStale`, `RestoreDrillStale`, `OffsiteCopyStale` (26 h digest) and
the `maintenance_runs` stamps exist to make silence visible — never disable
one to make a page stop. Backups run host-side (not in the compose app) via
`scripts/backup.sh` with `PRINTORIAN_BACKUP_ROOT` and
`PRINTORIAN_DB_PORT=5433`; WAL archives arrive gzip'd (27× compression
measured; `restore_command` gunzips). A failing drill is an incident
(ADR-0019), not a warning.

Evidence: docs/RUNBOOK-BACKUP-RESTORE.md §1–§2 (the uid-70/backup-init
lesson, the 1,385-failure / 23 GB WAL incident, the archiver check),
docs/adr/0019-backup-is-wal-archived-and-drilled.md.

Related: [[printorian-ports-5433-6380]].
