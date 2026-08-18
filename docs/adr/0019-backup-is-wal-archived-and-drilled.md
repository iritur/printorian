# ADR-0019 — Backup is WAL-archived, off-site, and drilled

**Status:** Accepted · 2026-08-11

## Context
ARCHITECTURE §10 promised "`pg_dump` + object-store snapshot on a schedule, with a
tested restore procedure". ROADMAP phase 7 promised a disaster-recovery drill.
Neither existed: what was there was a named Docker volume.

The gap that mattered was not the absence of backups but the recovery point of the
*planned* backup. A nightly dump means an outage at 23:00 loses a day of orders and
a day of `payment_notifications` — the record of what the payment gateway actually
told us, which is precisely what a money reconciliation has to replay.

## Decision
Three artifacts, and one job that proves they work.

1. **WAL archiving plus base backups.** RPO of roughly one minute rather than
   twenty-four hours. `archive_mode` is on in `docker-compose.yml` rather than left
   to the operator, because it cannot be enabled without a restart and a farm that
   has run for a year is the worst place to discover that.
2. **A nightly `pg_dump -Fc`**, verified with `pg_restore --list` at the moment it
   is written. A logical dump survives a class of disaster a physical one does not
   — block corruption, a bad major-version upgrade — and restores anywhere.
3. **An encrypted off-site copy.** A single on-prem box (ADR-0003) whose only
   backups are on that box is one fire from total loss.
4. **A scheduled restore drill** (`scripts/restore_drill.py`) that restores last
   night's dump into a scratch database, runs `alembic check` against it, and
   asserts the tables a recovery needs first are not empty.

## Consequences
* The third check in the drill is the one that earns its place: a backup script
  pointed at the wrong database produces a valid, restorable, **empty** dump every
  night, and every other check passes. That is the failure most likely to go
  unnoticed for months.
* A failing drill is an incident. The farm is running without a usable backup
  whatever the file listing says.
* **Backups are secret material.** The dump carries every `password_hash` and every
  `printers.access_code_encrypted`; it is encrypted at rest and in transit.
* **`PRINTORIAN_SECRET_KEY` is escrowed separately from the backups.** Printer
  access codes are encrypted with it (ADR-0014), so a restore without it returns
  every order and a fleet nobody can drive — a half-restore, discovered during the
  incident.
* Once blob storage lands (Slice A): blobs are written before the rows that
  reference them, and snapshotted before the database. Together those two make it
  impossible for a restored database to name a blob the restored store lacks.

Procedure lives in [RUNBOOK-BACKUP-RESTORE.md](../RUNBOOK-BACKUP-RESTORE.md).
